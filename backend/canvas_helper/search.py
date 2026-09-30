from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import html
from html.parser import HTMLParser
import re
import unicodedata
from typing import Any, Protocol

from sqlalchemy import delete, exists, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from .models import DocChunk, Document

_SPACE = re.compile(r"\s+")
_TOKENS = re.compile(r"[\w\u3400-\u9fff]+", re.UNICODE)
# CJK ideographs plus the kana ranges that share the "no word breaks" problem.
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _has_cjk(value: str) -> bool:
    return bool(_CJK.search(value))


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def plain_text(value: str) -> str:
    if "<" not in value:
        return value
    parser = _TextExtractor()
    parser.feed(value)
    return " ".join(parser.parts)


def normalize_query(value: str) -> str:
    """Normalize full-width/Unicode text without translating user intent."""
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", value)).strip()


# Words that carry no signal but, ANDed into a query, are enough to match
# nothing at all: "what assignments do I have" required every one of them.
_STOPWORDS = frozenset(
    "a an and any are as at be been by can could do does did for from get give "
    "has have how i in is it list me my of on or our show that the them then "
    "there these this to was were what when where which who why will with you "
    "your".split()
)
# Bigrams that are pure sentence scaffolding in Chinese. Dropping a bigram is
# safe in a way that dropping a character is not: it never splits a real word.
_CJK_STOP_BIGRAMS = frozenset(
    "看看 我有 有哪 哪些 一下 可以 怎么 什么 是不 不是 的话 请帮 帮我 我想 "
    "想要 告诉 诉我 需要 应该 我的 我们 你的 这个 那个 现在 还有 有没 没有 "
    "多少 什麼 哪個".split()
)


def search_terms(value: str) -> list[str]:
    """Split a query into the terms worth matching.

    Latin text splits on spaces, so its words are already terms. A Chinese
    sentence has no spaces: PostgreSQL tokenizes the whole thing as one word,
    which is why the old code fell back to matching the entire sentence as a
    single substring and found nothing. Overlapping 2-grams give it the word
    boundaries it lacks.
    """
    terms: list[str] = []
    for token in _TOKENS.findall(value):
        if _has_cjk(token):
            if len(token) <= 2:
                terms.append(token)
            else:
                terms.extend(
                    pair
                    for index in range(len(token) - 1)
                    if (pair := token[index : index + 2]) not in _CJK_STOP_BIGRAMS
                )
        elif token.casefold() not in _STOPWORDS:
            # Terms are matched as substrings, so dropping a plural "s" lets
            # "assignments" find an assignment without a stemmer.
            terms.append(token[:-1] if len(token) > 3 and token[-1] in "sS" else token)
    if not terms:
        # A query that is nothing but stopwords still deserves its best effort.
        terms = _TOKENS.findall(value)
    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        folded = term.casefold()
        if folded not in seen:
            seen.add(folded)
            unique.append(term)
    return unique[:12]


def _safe_snippet(value: str, query: str, limit: int = 260) -> str:
    clean = _SPACE.sub(" ", plain_text(value)).strip()
    terms = sorted(set(_TOKENS.findall(query)), key=len, reverse=True)
    first = min(
        (clean.casefold().find(term.casefold()) for term in terms if term and clean.casefold().find(term.casefold()) >= 0),
        default=0,
    )
    start = max(0, first - 80)
    excerpt = clean[start : start + limit]
    escaped = html.escape(excerpt)
    for term in terms:
        escaped = re.sub(
            re.escape(html.escape(term)),
            lambda match: f"<mark>{match.group(0)}</mark>",
            escaped,
            flags=re.IGNORECASE,
        )
    return ("…" if start else "") + escaped + ("…" if start + limit < len(clean) else "")


def split_chunks(content: str, *, kind: str, target: int = 1200, overlap: int = 160) -> list[dict[str, Any]]:
    """Split into stable, deterministic chunks; form-feeds preserve pages/slides."""
    content = plain_text(content).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        return []
    sections = content.split("\f")
    output: list[dict[str, Any]] = []
    ordinal = 0
    for section_index, section in enumerate(sections, 1):
        section = section.strip()
        cursor = 0
        while cursor < len(section):
            end = min(len(section), cursor + target)
            if end < len(section):
                boundary = max(section.rfind("\n", cursor, end), section.rfind(" ", cursor, end))
                if boundary > cursor + target // 2:
                    end = boundary
            value = section[cursor:end].strip()
            if value:
                item: dict[str, Any] = {"ordinal": ordinal, "content": value}
                if len(sections) > 1:
                    item["slide" if kind in {"pptx", "presentation"} else "page"] = section_index
                output.append(item)
                ordinal += 1
            if end >= len(section):
                break
            cursor = max(cursor + 1, end - overlap)
    return output


async def ensure_search_schema(engine: AsyncEngine) -> None:
    """Create SQLite-only FTS objects omitted by SQLAlchemy metadata.create_all.

    Search itself no longer reads this index: its tokenizer cannot see the
    2-grams that Chinese queries are matched on, so both dialects now score
    terms directly against the chunks. The index and its integrity check are
    left in place; dropping them needs a migration of its own.
    """
    if engine.dialect.name != "sqlite":
        return
    statements = [
        "CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5("
        "chunk_id UNINDEXED, user_id UNINDEXED, title, content, tokenize='trigram')",
        "CREATE TRIGGER IF NOT EXISTS doc_chunks_fts_insert AFTER INSERT ON doc_chunks BEGIN "
        "INSERT INTO search_index(rowid, chunk_id, user_id, title, content) "
        "VALUES (new.id, new.id, new.user_id, new.title, new.content); END",
        "CREATE TRIGGER IF NOT EXISTS doc_chunks_fts_delete AFTER DELETE ON doc_chunks BEGIN "
        "DELETE FROM search_index WHERE rowid = old.id; END",
        "CREATE TRIGGER IF NOT EXISTS doc_chunks_fts_update AFTER UPDATE ON doc_chunks BEGIN "
        "DELETE FROM search_index WHERE rowid = old.id; "
        "INSERT INTO search_index(rowid, chunk_id, user_id, title, content) "
        "VALUES (new.id, new.id, new.user_id, new.title, new.content); END",
    ]
    async with engine.begin() as connection:
        for statement in statements:
            await connection.exec_driver_sql(statement)


async def rebuild_document_chunks(session: AsyncSession, document: Document) -> int:
    """Atomically replace all chunks for one tenant-owned source."""
    source_kind = document.source_type or document.kind or "document"
    source_id = document.source_id or f"document:{document.id}"
    await session.execute(
        delete(DocChunk).where(
            DocChunk.user_id == document.user_id,
            DocChunk.source_kind == source_kind,
            DocChunk.source_id == source_id,
        )
    )
    kind = (
        "pptx" if (document.mime_type or "").endswith("presentationml.presentation")
        else document.kind
    )
    chunks = split_chunks(document.content or "", kind=kind)
    now = datetime.now(timezone.utc)
    for item in chunks:
        session.add(
            DocChunk(
                user_id=document.user_id,
                document_id=document.id,
                course_id=document.course_id,
                source_kind=source_kind,
                source_id=source_id,
                title=document.title,
                content=item["content"],
                ordinal=item["ordinal"],
                page=item.get("page"),
                slide=item.get("slide"),
                metadata_json=dict(document.metadata_json or {}),
                source_date=document.source_updated_at or document.updated_at,
                source_url=document.source_url,
                updated_at=now,
            )
        )
    await session.flush()
    return len(chunks)


async def backfill_search_chunks(session: AsyncSession) -> int:
    """Index documents created before the chunk migration."""
    documents = (
        await session.scalars(
            select(Document).where(
                ~exists().where(
                    DocChunk.user_id == Document.user_id,
                    DocChunk.document_id == Document.id,
                )
            )
        )
    ).all()
    count = 0
    for document in documents:
        count += await rebuild_document_chunks(session, document)
    if documents:
        await session.commit()
    return count


@dataclass(slots=True)
class SearchFilters:
    course_id: int | None = None
    kinds: tuple[str, ...] = ()
    date_from: datetime | None = None
    date_to: datetime | None = None
    document_id: int | None = None
    assignment_id: str | None = None


class VectorReranker(Protocol):
    async def rerank(
        self, query: str, rows: list[dict[str, Any]], top_k: int
    ) -> list[dict[str, Any]]: ...


class SearchService:
    def __init__(self, reranker: VectorReranker | None = None):
        self.reranker = reranker

    async def search(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        *,
        filters: SearchFilters | None = None,
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        filters = filters or SearchFilters()
        normalized = normalize_query(query)
        if not normalized:
            return []
        dialect = session.bind.dialect.name if session.bind is not None else "sqlite"
        params: dict[str, Any] = {"user_id": user_id, "limit": min(max(top_k, 1), 100)}
        where = ["c.user_id = :user_id"]
        if filters.course_id is not None:
            where.append("c.course_id = :course_id")
            params["course_id"] = filters.course_id
        if filters.kinds:
            labels = []
            for index, value in enumerate(filters.kinds):
                key = f"kind_{index}"
                labels.append(f":{key}")
                params[key] = value
            where.append(f"c.source_kind IN ({','.join(labels)})")
        if filters.date_from:
            where.append("c.source_date >= :date_from")
            params["date_from"] = filters.date_from
        if filters.date_to:
            where.append("c.source_date <= :date_to")
            params["date_to"] = filters.date_to
        if filters.document_id is not None:
            where.append("c.document_id = :document_id")
            params["document_id"] = filters.document_id
        if filters.assignment_id is not None:
            where.extend(["c.source_kind = 'assignment'", "c.source_id = :assignment_id"])
            params["assignment_id"] = filters.assignment_id

        columns = (
            "c.id, c.document_id, c.course_id, c.source_kind, c.source_id, "
            "c.title, c.content, c.ordinal, c.page, c.slide, c.source_date, c.source_url"
        )
        terms = search_terms(normalized)
        # Every term is optional and each one a row matches lifts it up the
        # ranking. Requiring all of them, as this used to, meant one throwaway
        # word in the question was enough to return nothing.
        like = "ILIKE" if dialect == "postgresql" else "LIKE"
        scores, matches = [], []
        for index, term in enumerate(terms):
            key = f"term_{index}"
            params[key] = f"%{term}%"
            scores.append(
                f"(CASE WHEN c.title {like} :{key} THEN 2 ELSE 0 END) + "
                f"(CASE WHEN c.content {like} :{key} THEN 1 ELSE 0 END)"
            )
            matches.append(f"c.title {like} :{key}")
            matches.append(f"c.content {like} :{key}")
        if not matches:
            return []
        nulls = " NULLS LAST" if dialect == "postgresql" else ""
        statement = text(
            f"SELECT {columns}, ({' + '.join(scores)}) AS rank "
            "FROM doc_chunks c WHERE "
            + " AND ".join(where)
            + f" AND ({' OR '.join(matches)}) "
            f"ORDER BY rank DESC, c.source_date DESC{nulls}, c.id LIMIT :limit"
        )
        records = (await session.execute(statement, params)).mappings().all()
        rows = [
            {
                **dict(row),
                "rank": float(row["rank"] or 0),
                "snippet": _safe_snippet(str(row["content"]), normalized),
                "citation": {
                    "source_id": str(row["source_id"]),
                    "kind": str(row["source_kind"]),
                    "document_id": row["document_id"],
                    "chunk_id": row["id"],
                    "page": row["page"],
                    "slide": row["slide"],
                    "title": row["title"],
                    "url": row["source_url"],
                },
            }
            for row in records
        ]
        if self.reranker:
            rows = await self.reranker.rerank(normalized, rows, top_k)
        return rows[:top_k]

    async def integrity_check(self, session: AsyncSession) -> bool:
        dialect = session.bind.dialect.name if session.bind is not None else "sqlite"
        if dialect != "sqlite":
            return True
        await session.execute(text("INSERT INTO search_index(search_index) VALUES('integrity-check')"))
        missing = await session.scalar(
            text(
                "SELECT count(*) FROM doc_chunks c LEFT JOIN search_index s "
                "ON s.rowid = c.id WHERE s.rowid IS NULL"
            )
        )
        return not missing


class RetrievalService:
    def __init__(self, search: SearchService | None = None):
        self.search_service = search or SearchService()

    async def retrieve(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        *,
        scope: str = "global",
        scope_id: str | int | None = None,
        top_k: int = 8,
    ) -> list[dict[str, Any]]:
        filters = SearchFilters()
        if scope == "course" and scope_id is not None:
            filters.course_id = int(scope_id)
        elif scope == "document" and scope_id is not None:
            filters.document_id = int(scope_id)
        elif scope == "assignment" and scope_id is not None:
            filters.assignment_id = str(scope_id)
        elif scope != "global":
            raise ValueError("Unsupported retrieval scope")
        return await self.search_service.search(
            session, user_id, query, filters=filters, top_k=top_k
        )

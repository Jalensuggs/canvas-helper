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


def _fts_query(value: str) -> str:
    # Quoting every token prevents FTS operators in user input from becoming SQL/FTS
    # syntax and works for both Latin words and Chinese phrases.
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in _TOKENS.findall(value))


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
    """Create SQLite-only FTS objects omitted by SQLAlchemy metadata.create_all."""
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
        if dialect == "postgresql":
            if _has_cjk(normalized):
                # to_tsvector('simple') splits on whitespace, so a Chinese
                # sentence is one token and no substring of it ever matches.
                # Trigram matching is what makes those queries work at all;
                # migration 0008 adds the GIN index that keeps it fast.
                params["pattern"] = f"%{normalized}%"
                statement = text(
                    f"SELECT {columns}, 0.0 AS rank FROM doc_chunks c WHERE "
                    + " AND ".join(where)
                    + " AND (c.title ILIKE :pattern OR c.content ILIKE :pattern) "
                    "ORDER BY c.source_date DESC NULLS LAST, c.id LIMIT :limit"
                )
            else:
                params["query"] = normalized
                statement = text(
                    f"SELECT {columns}, "
                    "ts_rank_cd(c.search_vector, websearch_to_tsquery('simple', :query)) AS rank "
                    "FROM doc_chunks c WHERE "
                    + " AND ".join(where)
                    + " AND c.search_vector @@ websearch_to_tsquery('simple', :query) "
                    "ORDER BY rank DESC, c.id LIMIT :limit"
                )
        else:
            fts = _fts_query(normalized)
            # Trigram FTS requires three-character terms. LIKE is a safe fallback
            # for very short queries, while all normal searches use the FTS index.
            if not fts or any(len(token) < 3 for token in _TOKENS.findall(normalized)):
                params["pattern"] = f"%{normalized}%"
                statement = text(
                    f"SELECT {columns}, 0.0 AS rank FROM doc_chunks c WHERE "
                    + " AND ".join(where)
                    + " AND (c.title LIKE :pattern OR c.content LIKE :pattern) "
                    "ORDER BY c.source_date DESC, c.id LIMIT :limit"
                )
            else:
                params["fts"] = fts
                statement = text(
                    f"SELECT {columns}, -bm25(search_index, 2.0, 1.0) AS rank "
                    "FROM search_index JOIN doc_chunks c ON c.id = search_index.rowid "
                    "WHERE search_index MATCH :fts AND "
                    + " AND ".join(where)
                    + " ORDER BY rank DESC, c.id LIMIT :limit"
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

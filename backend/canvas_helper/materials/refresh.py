from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import Document
from ..search import rebuild_document_chunks
from .extractors import extract_document
from .storage import MaterialStore

logger = logging.getLogger(__name__)


def extraction_failed(document: Document) -> bool:
    extraction = (document.metadata_json or {}).get("extraction") or {}
    return bool(extraction.get("error"))


def needs_extraction_refresh(document: Document) -> bool:
    mime = (document.mime_type or "").lower()
    if mime.startswith("image/"):
        return False
    if not document.local_path:
        return False
    if extraction_failed(document):
        return True
    return not bool(document.content)


def apply_extraction(document: Document, path: Path) -> None:
    extracted = extract_document(path, document.mime_type)
    now = datetime.now(timezone.utc)
    document.content = extracted.text
    document.preview_html = extracted.preview_html
    document.extracted_at = now
    document.updated_at = now
    metadata = dict(document.metadata_json or {})
    metadata["extraction"] = {**extracted.metadata, "error": extracted.error}
    document.metadata_json = metadata


async def refresh_document_extraction(
    session: AsyncSession, document: Document, store: MaterialStore
) -> bool:
    if not needs_extraction_refresh(document):
        return False
    try:
        path = store.resolve_local_path(document.local_path or "")
    except ValueError:
        return False
    if not path.is_file() or path.is_symlink():
        return False
    apply_extraction(document, path)
    await rebuild_document_chunks(session, document)
    return True


async def backfill_material_extractions(
    session: AsyncSession, materials_root: Path
) -> int:
    rows = list(
        (
            await session.scalars(
                select(Document).where(
                    Document.local_path.is_not(None),
                    or_(
                        Document.content.is_(None),
                        Document.content == "",
                    ),
                )
            )
        ).all()
    )
    count = 0
    for document in rows:
        store = MaterialStore(materials_root, document.user_id)
        count += int(await refresh_document_extraction(session, document, store))
    if count:
        await session.commit()
    return count


async def backfill_material_extractions_later(
    sessions: async_sessionmaker[AsyncSession], materials_root: Path
) -> int:
    try:
        async with sessions() as session:
            return await backfill_material_extractions(session, materials_root)
    except Exception:
        logger.exception("Background material extraction backfill failed")
        return 0

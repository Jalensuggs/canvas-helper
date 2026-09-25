from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
from urllib.parse import urljoin, urlsplit

import httpx

from ..canvas.client import CanvasClient
from ..security import safe_component, safe_join


@dataclass(slots=True)
class DownloadResult:
    path: Path
    sha256: str
    size: int
    mime_type: str | None
    version: int
    unchanged: bool = False
    preserved_path: Path | None = None


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    return (
        parsed.scheme.lower(),
        (parsed.hostname or "").lower(),
        parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
    )


class MaterialStore:
    def __init__(self, root: Path, user_id: str):
        self.root = safe_join(root, safe_component(user_id))
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def destination(self, course_id: int, file_id: str, filename: str) -> Path:
        folder = safe_join(self.root, f"course-{course_id}", f"file-{safe_component(file_id)}")
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        return safe_join(folder, safe_component(filename))

    def resolve_local_path(self, relative_path: str) -> Path:
        parts = Path(relative_path).parts
        if Path(relative_path).is_absolute() or not parts:
            raise ValueError("Invalid material path")
        return safe_join(self.root, *parts)

    def relative(self, path: Path) -> str:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise ValueError("Material path escapes tenant root")
        return str(resolved.relative_to(self.root.resolve()))

    async def download(
        self,
        canvas: CanvasClient,
        *,
        url: str,
        course_id: int,
        file_id: str,
        filename: str,
        prior_sha256: str | None = None,
        prior_version: int = 0,
        expected_size: int | None = None,
    ) -> DownloadResult:
        target = self.destination(course_id, file_id, filename)
        part = target.with_name(f".{target.name}.part")
        if part.exists():
            part.unlink()
        request_url = canvas._url(url)
        headers: dict[str, str] = {}
        base_origin = _origin(canvas.base_url)
        size = 0
        digest = hashlib.sha256()
        try:
            for redirect_count in range(6):
                if urlsplit(request_url).scheme.lower() != "https":
                    raise ValueError("Material downloads require HTTPS")
                if _origin(request_url) != base_origin:
                    headers.pop("Authorization", None)
                async with canvas._client.stream(
                    "GET", request_url, headers=headers, follow_redirects=False
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if redirect_count >= 5:
                            raise httpx.TooManyRedirects("More than 5 redirects")
                        location = response.headers.get("location")
                        if not location:
                            response.raise_for_status()
                        request_url = urljoin(request_url, location)
                        continue
                    response.raise_for_status()
                    content_length = response.headers.get("content-length")
                    if expected_size is not None and content_length:
                        if int(content_length) != expected_size:
                            raise ValueError("Canvas file size does not match metadata")
                    with part.open("xb") as output:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            digest.update(chunk)
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    mime_type = response.headers.get("content-type", "").split(";", 1)[0] or None
                    break
            else:
                raise httpx.TooManyRedirects("More than 5 redirects")

            if expected_size is not None and size != expected_size:
                raise ValueError("Downloaded file size does not match Canvas metadata")
            sha256 = digest.hexdigest()
            if target.exists() and prior_sha256 == sha256:
                current_hash = hashlib.sha256(target.read_bytes()).hexdigest()
                if current_hash == prior_sha256:
                    part.unlink()
                    return DownloadResult(
                        target, sha256, size, mime_type, max(1, prior_version), unchanged=True
                    )

            preserved: Path | None = None
            next_version = max(1, prior_version + (1 if target.exists() else 0))
            if target.exists():
                current_hash = hashlib.sha256(target.read_bytes()).hexdigest()
                versions = safe_join(target.parent, "versions")
                versions.mkdir(mode=0o700, exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                label = "user" if prior_sha256 and current_hash != prior_sha256 else f"v{max(1, prior_version)}"
                preserved = safe_join(versions, f"{label}-{stamp}-{target.name}")
                shutil.copy2(target, preserved)
            os.replace(part, target)
            return DownloadResult(target, sha256, size, mime_type, next_version, preserved_path=preserved)
        finally:
            if part.exists():
                part.unlink()

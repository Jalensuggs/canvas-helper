"""Frozen desktop sidecar entrypoint with atomic port discovery."""

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import tempfile

import uvicorn

from .config import Settings
from .main import create_app


def acquire_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = path.open("a+")
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("desktop backend is already running") from None
    return handle


def write_discovery(path: Path, port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".backend-", text=True)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({"host": "127.0.0.1", "port": port, "pid": os.getpid()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--discovery-file", type=Path, required=True)
    args = parser.parse_args()

    data_dir = args.data_dir.expanduser().resolve()
    lock = acquire_lock(data_dir / "backend.lock")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    settings = Settings(
        deployment_mode="local_desktop",
        environment="production",
        database_url=f"sqlite+aiosqlite:///{data_dir / 'canvas_helper.db'}",
        data_dir=data_dir,
        public_url=origin,
        allowed_origins=(origin,),
    )
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="info")
    )
    write_discovery(args.discovery_file, port)
    try:
        server.run(sockets=[listener])
    finally:
        args.discovery_file.unlink(missing_ok=True)
        listener.close()
        lock.close()


if __name__ == "__main__":
    main()

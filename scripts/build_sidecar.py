#!/usr/bin/env python3
"""Freeze the backend and name it as a Tauri external binary."""

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def target_triple() -> str:
    if value := os.environ.get("TARGET_TRIPLE"):
        return value
    result = subprocess.run(
        ["rustc", "-vV"], check=True, capture_output=True, text=True
    )
    for line in result.stdout.splitlines():
        if line.startswith("host: "):
            return line.removeprefix("host: ")
    raise RuntimeError("rustc did not report a host target")


def main() -> None:
    triple = target_triple()
    name = f"canvas-helper-backend-{triple}"
    separator = ";" if os.name == "nt" else ":"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--onefile",
            "--name",
            name,
            "--paths",
            str(ROOT / "backend"),
            "--collect-submodules",
            "keyring.backends",
            "--hidden-import",
            "aiosqlite",
            "--hidden-import",
            "sqlalchemy.dialects.sqlite.aiosqlite",
            "--add-data",
            f"{ROOT / 'frontend' / 'dist'}{separator}frontend/dist",
            "--add-data",
            f"{ROOT / 'migrations'}{separator}migrations",
            "--add-data",
            f"{ROOT / 'alembic.ini'}{separator}.",
            str(ROOT / "scripts" / "sidecar_entry.py"),
        ],
        cwd=ROOT,
        check=True,
    )
    destination = ROOT / "src-tauri" / "binaries"
    destination.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if os.name == "nt" else ""
    source = ROOT / "dist" / f"{name}{suffix}"
    source.replace(destination / source.name)


if __name__ == "__main__":
    main()

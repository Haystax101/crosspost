"""Wiring: environment/secrets -> store and API clients. Shared by run, auth and backfill."""

from __future__ import annotations

import os
from pathlib import Path

from . import config as config_mod
from .clients import google
from .store import DriveStore


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader for local runs (KEY=VALUE lines). Real env vars win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def set_dotenv(key: str, value: str, path: str | Path = ".env") -> None:
    p = Path(path)
    lines = [l for l in (p.read_text().splitlines() if p.exists() else []) if not l.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    p.write_text("\n".join(lines) + "\n")
    p.chmod(0o600)
    os.environ[key] = value


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing environment variable {name} (GitHub Secret, or .env locally; see README)")
    return value


def google_creds():
    return google.credentials(env("GOOGLE_CLIENT_ID"), env("GOOGLE_CLIENT_SECRET"), env("GOOGLE_REFRESH_TOKEN"))


def drive() -> google.Drive:
    return google.Drive(google_creds())


def store(cfg: config_mod.Config, drv: google.Drive | None = None) -> DriveStore:
    return DriveStore(drv or drive(), cfg.drive.state_folder, cfg.drive.state_file, env("CROSSPOST_STATE_KEY"))

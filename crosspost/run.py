"""Scheduled job entry point: python -m crosspost.run [--dry-run] [--config config.yaml]

Exit status: 0 when the run completed (including "nothing new"), 1 on a real
failure (a platform gave up, a post never matched, auth broke, ...).
"""

from __future__ import annotations

import argparse
import copy
import sys

from . import app, config, log, media
from .clients import google
from .clients.instagram import Instagram
from .clients.tiktok import TikTok
from .job import Clients, Job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crosspost.run", description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="do everything except publish/move/save progress")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    log.setup(args.verbose)
    app.load_dotenv()

    try:
        cfg = config.load(args.config)
        creds = app.google_creds()
        drive = google.Drive(creds)
        store = app.store(cfg, drive)
        state = store.load()
        # In dry-run we work on a copy so nothing but rotated tokens is persisted.
        work = copy.deepcopy(state) if args.dry_run else state

        def save_tokens(platform: str):
            def save(tokens: dict) -> None:
                state.tokens[platform] = work.tokens[platform] = tokens
                store.save(state)
                log.info("tokens refreshed and saved", platform=platform)
            return save

        def checkpoint() -> None:
            if not args.dry_run:
                store.save(state)

        clients = Clients(
            tiktok=TikTok(app.env("TIKTOK_CLIENT_KEY"), app.env("TIKTOK_CLIENT_SECRET"),
                          work.tokens.get("tiktok", {}), save_tokens("tiktok")),
            drive=drive,
            instagram=Instagram(work.tokens.get("instagram", {}), save_tokens("instagram"), cfg.instagram.api_version)
            if cfg.instagram.enabled else None,
            youtube=google.YouTube(creds) if cfg.youtube.enabled else None,
            probe=media.probe,
        )
        return Job(cfg, work, clients, checkpoint, dry_run=args.dry_run).run()
    except Exception as e:
        log.error("run crashed", exc_info=True, error=e)
        return 1


if __name__ == "__main__":
    sys.exit(main())

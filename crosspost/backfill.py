"""Backfill older TikToks (run locally: TikTok often blocks datacenter IPs).

    python -m crosspost.backfill --dry-run     # list eligible TikToks + Instagram caption matches; touch nothing
    python -m crosspost.backfill               # same, then download candidates to backfill/ for spot-checking
    python -m crosspost.backfill approve --all # upload every candidate still in backfill/ and queue it
    python -m crosspost.backfill approve ID..  # ...or just these
    python -m crosspost.backfill status        # what's queued / pending / failed

To reject a candidate (e.g. it has a watermark), delete its file from backfill/
before `approve --all`. The scheduled job drains the queue oldest first, within
backfill.max_per_day, the Instagram publishing limit and the YouTube quota.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict
from difflib import SequenceMatcher
from pathlib import Path

from . import app, config, log
from .clients.google import Drive
from .clients.instagram import Instagram
from .clients.tiktok import TikTok, TikTokVideo
from .state import BACKFILL, Video

MANIFEST = "candidates.json"
POSSIBLE_MATCH_MARGIN = 0.15


# --- pure helpers (tested) --------------------------------------------------

def normalize_caption(text: str | None) -> str:
    """Lowercase words only: hashtags, mentions, punctuation and emoji removed."""
    text = re.sub(r"[#@]\w+", " ", (text or "").lower())
    return " ".join(re.findall(r"\w+", text))


def best_caption_match(caption: str, ig_media: list[dict]) -> tuple[float, dict | None]:
    norm = normalize_caption(caption)
    if not norm:
        return 0.0, None
    best, best_item = 0.0, None
    for item in ig_media:
        other = normalize_caption(item.get("caption"))
        if not other:
            continue
        ratio = SequenceMatcher(None, norm, other).ratio()
        if ratio > best:
            best, best_item = ratio, item
    return best, best_item


def pick_format(formats: list[dict]) -> tuple[str, bool]:
    """Choose a yt-dlp format id, preferring watermark-free. Returns (format_id, watermark_free)."""
    video = [f for f in formats if f.get("format_id") and (f.get("vcodec") or "") != "none"]
    if not video:
        return "best", False

    def watermarked(f: dict) -> bool:
        return "watermark" in (f.get("format_note") or "").lower() or f["format_id"].startswith("download")

    clean = [f for f in video if not watermarked(f)]
    pool = clean or video
    best = max(pool, key=lambda f: (f.get("height") or 0,
                                    (f.get("vcodec") or "").startswith(("h264", "avc")),
                                    f.get("tbr") or 0))
    return best["format_id"], bool(clean)


# --- commands -----------------------------------------------------------------

def _download(url: str, out_dir: Path, video_id: str) -> tuple[Path, bool]:
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    fmt, clean = pick_format(info.get("formats") or [])
    with yt_dlp.YoutubeDL({"format": fmt, "outtmpl": str(out_dir / f"{video_id}.%(ext)s"), "quiet": True}) as ydl:
        ydl.download([url])
    return next(out_dir.glob(f"{video_id}.*")), clean


def scan(cfg: config.Config, dry_run: bool) -> int:
    drive = app.drive()
    store = app.store(cfg, drive)
    state = store.load()

    def save_tokens(platform):
        def save(tokens):
            state.tokens[platform] = tokens
            store.save(state)  # rotated tokens must persist even in dry-run
        return save

    tt = TikTok(app.env("TIKTOK_CLIENT_KEY"), app.env("TIKTOK_CLIENT_SECRET"), state.tokens.get("tiktok", {}), save_tokens("tiktok"))
    ig = Instagram(state.tokens.get("instagram", {}), save_tokens("instagram"), cfg.instagram.api_version)

    videos = list(tt.iter_videos())
    eligible = [v for v in videos if v.duration is not None and v.duration <= cfg.backfill.max_duration_s
                and v.id not in state.videos]
    print(f"{len(videos)} TikToks total, {len(eligible)} ≤{cfg.backfill.max_duration_s:g}s and not yet in state.")
    ig_media = list(ig.iter_media())
    print(f"{len(ig_media)} posts on Instagram.\n")

    threshold = cfg.backfill.caption_match_threshold
    candidates: list[TikTokVideo] = []
    for v in sorted(eligible, key=lambda v: v.create_time):
        ratio, item = best_caption_match(v.caption, ig_media)
        when = time.strftime("%Y-%m-%d", time.gmtime(v.create_time))
        head = f"{v.id} {when} {v.duration:>5.0f}s  {v.caption[:60]!r}"
        if item and ratio >= threshold:
            print(f"SKIP (on IG, {ratio:.2f})  {head}\n      ↳ IG {item.get('permalink')}  {item.get('caption', '')[:60]!r}")
            continue
        if item and ratio >= threshold - POSSIBLE_MATCH_MARGIN:
            print(f"CHECK (maybe on IG, {ratio:.2f})  {head}\n      ↳ IG {item.get('permalink')}  {item.get('caption', '')[:60]!r}")
        else:
            print(f"NEW  {head}")
        candidates.append(v)

    print(f"\n{len(candidates)} candidate(s).")
    if dry_run:
        print("Dry run: nothing downloaded.")
        return 0

    out = Path(cfg.backfill.local_dir)
    out.mkdir(exist_ok=True)
    manifest_path = out / MANIFEST
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    failures = 0
    for v in candidates:
        if any(out.glob(f"{v.id}.*")) and v.id in manifest:
            continue
        try:
            path, clean = _download(v.share_url, out, v.id)
        except Exception as e:
            failures += 1
            log.error("download failed", tiktok_id=v.id, url=v.share_url, error=e)
            continue
        manifest[v.id] = {**asdict(v), "file": path.name, "watermark_free_format": clean}
        manifest_path.write_text(json.dumps(manifest, indent=2))
        print(f"downloaded {path}" + ("" if clean else "  ⚠ no watermark-free format found: check this one"))
    print(f"\nSpot-check the files in {out}/, delete any you don't want, then run: python -m crosspost.backfill approve --all")
    return 1 if failures else 0


def approve(cfg: config.Config, ids: list[str], all_: bool, dry_run: bool) -> int:
    out = Path(cfg.backfill.local_dir)
    manifest_path = out / MANIFEST
    if not manifest_path.exists():
        raise SystemExit(f"no {manifest_path}: run `python -m crosspost.backfill` first")
    manifest = json.loads(manifest_path.read_text())
    if all_:
        ids = [i for i, m in manifest.items() if (out / m["file"]).exists()]
    drive: Drive = app.drive()
    store = app.store(cfg, drive)
    state = store.load()
    folder = None if dry_run else drive.folder_id(f"{cfg.drive.inbox_folder}/{cfg.drive.backfill_folder}", create=True)
    queued = 0
    for vid in ids:
        m = manifest.get(vid)
        if m is None or not (out / m["file"]).exists():
            print(f"{vid}: no downloaded file, skipping")
            continue
        if vid in state.videos:
            print(f"{vid}: already in state, skipping")
            continue
        if dry_run:
            print(f"{vid}: would upload {m['file']} and queue")
            continue
        file_id = drive.upload(folder, out / m["file"], m["file"])
        state.add_video(
            Video(id=vid, create_time=m["create_time"], caption=m["caption"], duration=m["duration"],
                  share_url=m["share_url"], source=BACKFILL, first_seen=int(time.time()), drive_file_id=file_id),
            cfg.enabled_platforms(),
        )
        store.save(state)
        queued += 1
        print(f"{vid}: queued")
    print(f"\n{queued} queued. The scheduled job will drain them oldest first.")
    return 0


def status(cfg: config.Config) -> int:
    state = app.store(cfg).load()
    rows = [v for v in state.videos.values() if not v.settled() or v.flagged
            or any(s.status == "failed" for s in v.platforms.values())]
    if not rows:
        print(f"{len(state.videos)} videos tracked; nothing pending or failed.")
    for v in sorted(rows, key=lambda v: v.create_time):
        plats = " ".join(f"{p}={s.status}({s.attempts})" for p, s in v.platforms.items())
        flag = " FLAGGED-UNMATCHED" if v.flagged else ""
        print(f"{v.id} {v.source:8} {plats}{flag}  {v.caption[:50]!r}")
    for name in ("youtube_uploads", "backfill_instagram", "backfill_youtube"):
        print(f"today {name}: {state.count(name, time.time())}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crosspost.backfill", description="Backfill older TikToks.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="cmd")
    ap = sub.add_parser("approve", help="upload downloaded candidates to Drive and queue them")
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true", dest="approve_dry_run")
    sub.add_parser("status", help="show queued, pending and failed items")
    args = parser.parse_args(argv)
    log.setup()
    app.load_dotenv()
    cfg = config.load(args.config)
    if args.cmd == "approve":
        if not args.ids and not args.all:
            parser.error("approve needs IDs or --all")
        return approve(cfg, args.ids, args.all, args.dry_run or args.approve_dry_run)
    if args.cmd == "status":
        return status(cfg)
    return scan(cfg, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

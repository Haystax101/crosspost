"""Pipeline state: which TikToks we've seen, per-platform progress, tokens, counters.

The whole thing is one JSON document (encrypted at rest, see store.py). Keep it
small and boring: plain dataclasses that round-trip through dicts.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

PLATFORMS = ("instagram", "youtube")

PENDING = "pending"
DONE = "done"
FAILED = "failed"  # gave up after max attempts

LIVE = "live"
BACKFILL = "backfill"

# YouTube's quota resets at midnight Pacific; daily caps use the same day boundary.
QUOTA_TZ = ZoneInfo("America/Los_Angeles")


@dataclass
class PlatformStatus:
    status: str = PENDING
    attempts: int = 0
    post_id: str | None = None
    last_error: str | None = None
    updated_at: int | None = None


@dataclass
class Video:
    id: str
    create_time: int
    caption: str
    duration: float | None
    share_url: str = ""
    source: str = LIVE
    first_seen: int = 0
    drive_file_id: str | None = None
    flagged: bool = False  # live post never matched a Drive file within the retry window
    archived: bool = False  # Drive file moved to the done folder
    platforms: dict[str, PlatformStatus] = field(default_factory=dict)

    def pending_platforms(self) -> list[str]:
        return [p for p, s in self.platforms.items() if s.status == PENDING]

    def settled(self) -> bool:
        return not self.pending_platforms()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Video:
        d = dict(d)
        d["platforms"] = {p: PlatformStatus(**s) for p, s in d.get("platforms", {}).items()}
        return cls(**d)


@dataclass
class State:
    version: int = 1
    # Only TikToks created at/after this time are picked up by the scheduled job;
    # older ones are the backfill command's business. Set on the first run.
    watermark: int | None = None
    videos: dict[str, Video] = field(default_factory=dict)
    # platform -> token dict (see clients/tiktok.py, clients/instagram.py)
    tokens: dict[str, dict[str, Any]] = field(default_factory=dict)
    # counter name -> {"day": "YYYY-MM-DD", "count": n}
    counters: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Drive file id -> {"time": unix|None, "duration": float|None}; avoids re-downloading to ffprobe
    probes: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add_video(self, video: Video, platforms: list[str]) -> Video:
        video.platforms = {p: PlatformStatus() for p in platforms}
        self.videos[video.id] = video
        return video

    # --- daily counters -------------------------------------------------

    @staticmethod
    def day(now: float) -> str:
        return datetime.fromtimestamp(now, QUOTA_TZ).strftime("%Y-%m-%d")

    def count(self, name: str, now: float) -> int:
        c = self.counters.get(name)
        return c["count"] if c and c["day"] == self.day(now) else 0

    def bump(self, name: str, now: float) -> None:
        self.counters[name] = {"day": self.day(now), "count": self.count(name, now) + 1}

    # --- serialisation --------------------------------------------------

    def to_json(self) -> bytes:
        return json.dumps(dataclasses.asdict(self), sort_keys=True).encode()

    @classmethod
    def from_json(cls, raw: bytes) -> State:
        d = json.loads(raw)
        if d.get("version") != 1:
            raise ValueError(f"unsupported state version {d.get('version')!r}")
        d["videos"] = {k: Video.from_dict(v) for k, v in d.get("videos", {}).items()}
        return cls(**d)

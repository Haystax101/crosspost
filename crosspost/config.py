"""Load config.yaml into typed, read-only settings."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class DriveConfig:
    inbox_folder: str = "tiktok-inbox"
    done_folder: str = "done"
    backfill_folder: str = "backfill"
    state_folder: str = "crosspost-state"
    state_file: str = "state.json.enc"


@dataclass(frozen=True)
class MatchingConfig:
    window_minutes: float = 30
    duration_tolerance_s: float = 1.0
    unmatched_retry_hours: float = 24


@dataclass(frozen=True)
class CaptionConfig:
    strip_tags: tuple[str, ...] = ("fyp", "foryou", "foryoupage", "fy", "viral")
    instagram_max_chars: int = 2200
    instagram_max_hashtags: int = 30
    youtube_title_max_chars: int = 100
    youtube_default_title: str = "New video"


@dataclass(frozen=True)
class InstagramConfig:
    enabled: bool = True
    api_version: str = "v24.0"
    share_to_feed: bool = True
    poll_interval_s: float = 10
    poll_timeout_s: float = 600
    refresh_days_before_expiry: float = 15


@dataclass(frozen=True)
class YouTubeConfig:
    enabled: bool = True
    privacy_status: str = "private"
    category_id: str = "22"
    made_for_kids: bool = False
    daily_upload_limit: int = 6


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 5


@dataclass(frozen=True)
class BackfillConfig:
    max_duration_s: float = 180
    local_dir: str = "backfill"
    caption_match_threshold: float = 0.85
    max_per_day: dict[str, int] = field(default_factory=lambda: {"instagram": 5, "youtube": 3})


@dataclass(frozen=True)
class Config:
    drive: DriveConfig = DriveConfig()
    matching: MatchingConfig = MatchingConfig()
    captions: CaptionConfig = CaptionConfig()
    instagram: InstagramConfig = InstagramConfig()
    youtube: YouTubeConfig = YouTubeConfig()
    retries: RetryConfig = RetryConfig()
    backfill: BackfillConfig = BackfillConfig()

    def enabled_platforms(self) -> list[str]:
        return [p for p in ("instagram", "youtube") if getattr(self, p).enabled]


def _build(cls: type, data: dict[str, Any] | None, where: str) -> Any:
    """Build a dataclass from a dict, rejecting unknown keys so typos fail loudly."""
    data = data or {}
    fields = {f.name: f for f in dataclasses.fields(cls)}
    unknown = set(data) - set(fields)
    if unknown:
        raise ValueError(f"unknown config key(s) in {where}: {', '.join(sorted(unknown))}")
    kwargs = {}
    for name, value in data.items():
        default = getattr(cls(), name)
        if dataclasses.is_dataclass(default):
            value = _build(type(default), value, f"{where}.{name}")
        elif isinstance(default, tuple):
            value = tuple(value)
        kwargs[name] = value
    return cls(**kwargs)


def load(path: str | Path = "config.yaml") -> Config:
    with open(path) as f:
        cfg = _build(Config, yaml.safe_load(f), "config")
    if cfg.youtube.privacy_status not in ("private", "unlisted", "public"):
        raise ValueError("youtube.privacy_status must be private, unlisted or public")
    return cfg

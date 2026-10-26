"""File metadata: duration and timestamp via ffprobe, and the Shortcut's filename stamp."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# The iOS Shortcut names files like tt-20261026T161500+0100.mov (see SHORTCUT.md).
NAME_STAMP = re.compile(r"(\d{8}T\d{6}[+-]\d{4})")


@dataclass(frozen=True)
class Probe:
    duration: float | None
    time: int | None


def parse_time(value: str | None) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None  # ambiguous; better to fall back than to guess a timezone
    return int(dt.timestamp())


def time_from_name(name: str) -> int | None:
    m = NAME_STAMP.search(name)
    if not m:
        return None
    return int(datetime.strptime(m.group(1), "%Y%m%dT%H%M%S%z").timestamp())


def parse_ffprobe(output: str) -> Probe:
    data = json.loads(output)
    fmt = data.get("format", {})
    duration = float(fmt["duration"]) if fmt.get("duration") else None
    tags = {k.lower(): v for k, v in fmt.get("tags", {}).items()}
    # Apple's creationdate carries the local offset and is the true capture/save
    # time; the generic creation_time is UTC.
    time = parse_time(tags.get("com.apple.quicktime.creationdate")) or parse_time(tags.get("creation_time"))
    return Probe(duration, time)


def probe(path: Path) -> Probe:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return parse_ffprobe(out)

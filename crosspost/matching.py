"""Match TikTok posts to Drive files by timestamp AND duration. Pure, no I/O."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Post:
    id: str
    create_time: int
    duration: float | None


@dataclass(frozen=True)
class FileInfo:
    id: str
    time: int | None  # best-known capture/save time, unix seconds
    duration: float | None


def match(posts: list[Post], files: list[FileInfo], window_s: float, tolerance_s: float) -> dict[str, str]:
    """Return {post_id: file_id}. Each post and each file is used at most once.

    A pair qualifies only if both the time gap is within `window_s` and the
    duration gap is within `tolerance_s`. Among qualifying pairs, the closest in
    time wins (then closest in duration), so two quick posts don't swap files.
    """
    pairs = []
    for p in posts:
        if p.duration is None:
            continue
        for f in files:
            if f.time is None or f.duration is None:
                continue
            dt = abs(p.create_time - f.time)
            dd = abs(p.duration - f.duration)
            if dt <= window_s and dd <= tolerance_s:
                pairs.append((dt, dd, p.id, f.id))
    result: dict[str, str] = {}
    used: set[str] = set()
    for _, _, post_id, file_id in sorted(pairs):
        if post_id not in result and file_id not in used:
            result[post_id] = file_id
            used.add(file_id)
    return result

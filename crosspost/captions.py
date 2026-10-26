"""Per-platform caption transforms. Pure functions, no I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import CaptionConfig

HASHTAG = re.compile(r"(?<![\w#])#(\w+)")
# YouTube rejects '<' and '>' in titles and descriptions.
YT_FORBIDDEN = re.compile(r"[<>]")
SENTENCE_END = re.compile(r"(?<=[.!?])\s")
YOUTUBE_DESCRIPTION_MAX_BYTES = 5000
SHORTS_TAG = "#Shorts"


def _tidy(text: str) -> str:
    """Collapse runs of spaces, trim each line, and drop leading/trailing blank lines."""
    lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def strip_tags(text: str, tags: tuple[str, ...] | list[str]) -> str:
    """Remove the given hashtags (case-insensitive, given without '#')."""
    drop = {t.lower().lstrip("#") for t in tags}
    return _tidy(HASHTAG.sub(lambda m: "" if m.group(1).lower() in drop else m.group(0), text))


def _truncate(text: str, limit: int) -> str:
    """Cut to at most `limit` chars at a word boundary, ending with an ellipsis."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:-") + "…"


def instagram_caption(raw: str, cfg: CaptionConfig) -> str:
    text = strip_tags(raw, cfg.strip_tags)
    seen = 0

    def keep(m: re.Match) -> str:
        nonlocal seen
        seen += 1
        return m.group(0) if seen <= cfg.instagram_max_hashtags else ""

    text = _tidy(HASHTAG.sub(keep, text))
    return _truncate(text, cfg.instagram_max_chars)


def youtube_title(raw: str, cfg: CaptionConfig) -> str:
    text = YT_FORBIDDEN.sub("", strip_tags(raw, cfg.strip_tags))
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    title = SENTENCE_END.split(first_line, maxsplit=1)[0].strip() or cfg.youtube_default_title
    limit = cfg.youtube_title_max_chars
    title = _truncate(title, limit)
    if SHORTS_TAG.lower() not in title.lower() and len(title) + 1 + len(SHORTS_TAG) <= limit:
        title = f"{title} {SHORTS_TAG}"
    return title


def youtube_description(raw: str, cfg: CaptionConfig) -> str:
    text = YT_FORBIDDEN.sub("", strip_tags(raw, cfg.strip_tags))
    while len(text.encode()) > YOUTUBE_DESCRIPTION_MAX_BYTES:
        text = _truncate(text, len(text) - 50)
    return text


@dataclass(frozen=True)
class Captions:
    instagram: str
    youtube_title: str
    youtube_description: str


def transform(raw: str, cfg: CaptionConfig) -> Captions:
    return Captions(instagram_caption(raw, cfg), youtube_title(raw, cfg), youtube_description(raw, cfg))

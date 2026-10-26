import json

import pytest

from crosspost import config
from crosspost.media import parse_ffprobe, time_from_name


def test_repo_config_loads():
    cfg = config.load("config.yaml")
    assert cfg.youtube.privacy_status == "private"
    assert "fyp" in cfg.captions.strip_tags
    assert cfg.enabled_platforms() == ["instagram", "youtube"]


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("youtube:\n  privacy: public\n")
    with pytest.raises(ValueError, match="youtube"):
        config.load(p)


def test_bad_privacy_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("youtube:\n  privacy_status: secret\n")
    with pytest.raises(ValueError):
        config.load(p)


def test_partial_config_keeps_defaults(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("matching:\n  window_minutes: 10\n")
    cfg = config.load(p)
    assert cfg.matching.window_minutes == 10 and cfg.matching.duration_tolerance_s == 1.0


def test_parse_ffprobe_prefers_apple_creationdate():
    out = json.dumps({"format": {"duration": "15.234", "tags": {
        "creation_time": "2026-10-26T16:15:09.000000Z",
        "com.apple.quicktime.creationdate": "2026-10-26T17:15:00+0100"}}})
    p = parse_ffprobe(out)
    assert p.duration == pytest.approx(15.234)
    assert p.time == 1793030400 + 900  # 16:15:00Z


def test_parse_ffprobe_missing_fields():
    p = parse_ffprobe(json.dumps({"format": {}}))
    assert p.duration is None and p.time is None


def test_time_from_name():
    assert time_from_name("tt-20261026T171500+0100.mov") == 1793031300
    assert time_from_name("IMG_1234.MOV") is None

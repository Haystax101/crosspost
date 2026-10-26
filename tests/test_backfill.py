from crosspost.backfill import best_caption_match, normalize_caption, pick_format


def test_normalize_drops_tags_mentions_punctuation():
    assert normalize_caption("Sunset RUN!! 🌅 #fyp @friend #running") == "sunset run"
    assert normalize_caption(None) == ""


def test_best_caption_match_ignores_hashtag_differences():
    ig = [{"id": "1", "caption": "Something else entirely"},
          {"id": "2", "caption": "Sunset run!! #running"}]
    ratio, item = best_caption_match("Sunset run!! #fyp #running #viral", ig)
    assert item["id"] == "2" and ratio == 1.0


def test_best_caption_match_empty_caption_never_matches():
    assert best_caption_match("#fyp", [{"caption": "#fyp"}]) == (0.0, None)


def test_pick_format_prefers_watermark_free_then_resolution_then_h264():
    formats = [
        {"format_id": "download", "format_note": "watermarked", "vcodec": "h264", "height": 1080},
        {"format_id": "bytevc1_720p", "vcodec": "h265", "height": 720, "tbr": 900},
        {"format_id": "h264_720p", "vcodec": "h264", "height": 720, "tbr": 800},
        {"format_id": "h264_540p", "vcodec": "h264", "height": 540, "tbr": 2000},
        {"format_id": "audio", "vcodec": "none"},
    ]
    assert pick_format(formats) == ("h264_720p", True)


def test_pick_format_falls_back_to_watermarked_and_says_so():
    formats = [{"format_id": "download", "format_note": "Watermarked", "vcodec": "h264", "height": 1080}]
    assert pick_format(formats) == ("download", False)

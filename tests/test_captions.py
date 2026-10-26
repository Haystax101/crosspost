from crosspost import captions
from crosspost.config import CaptionConfig

CFG = CaptionConfig()


def test_strip_tags_is_case_insensitive_and_keeps_others():
    out = captions.strip_tags("Morning run 🏃 #FYP #running #foryou #ForYouPage #fy #viral", CFG.strip_tags)
    assert out == "Morning run 🏃 #running"


def test_strip_tags_does_not_touch_longer_tags_or_words():
    assert captions.strip_tags("#fypage #fyp2 fyp #viralvideo", CFG.strip_tags) == "#fypage #fyp2 fyp #viralvideo"


def test_strip_tags_tidies_lines():
    assert captions.strip_tags("Line one #fyp\n\n\n\n#fyp #cats  here", CFG.strip_tags) == "Line one\n\n#cats here"


def test_instagram_keeps_only_30_hashtags():
    raw = "Hi " + " ".join(f"#t{i}" for i in range(40))
    out = captions.instagram_caption(raw, CFG)
    assert out.count("#") == 30
    assert "#t29" in out and "#t30" not in out


def test_instagram_truncates_to_2200_chars():
    out = captions.instagram_caption("word " * 1000, CFG)
    assert len(out) <= 2200
    assert out.endswith("…")


def test_youtube_title_first_sentence_plus_shorts():
    raw = "Best pasta ever! Recipe below.\nStep 1 boil #food #fyp"
    assert captions.youtube_title(raw, CFG) == "Best pasta ever! #Shorts"


def test_youtube_title_first_line():
    assert captions.youtube_title("Day 3 of training\nmore text", CFG) == "Day 3 of training #Shorts"


def test_youtube_title_long_is_truncated_at_word_boundary():
    title = captions.youtube_title("a" * 50 + " " + "b" * 60, CFG)
    assert title == "a" * 50 + "… #Shorts"


def test_youtube_title_long_single_word_has_no_room_for_shorts():
    title = captions.youtube_title("a" * 150, CFG)
    assert len(title) == 100 and "#Shorts" not in title


def test_youtube_title_shorts_added_only_if_fits():
    raw = "x" * 92  # 92 + " #Shorts" (8) = 100 fits exactly
    assert captions.youtube_title(raw, CFG) == raw + " #Shorts"
    raw = "x" * 93
    assert captions.youtube_title(raw, CFG) == raw


def test_youtube_title_not_duplicating_shorts():
    assert captions.youtube_title("Quick tip #shorts", CFG) == "Quick tip #shorts"


def test_youtube_title_falls_back_when_caption_empty():
    assert captions.youtube_title("#fyp #viral", CFG) == "New video #Shorts"
    assert captions.youtube_title("", CFG) == "New video #Shorts"


def test_youtube_strips_angle_brackets():
    c = captions.transform("I <3 this > that", CFG)
    assert "<" not in c.youtube_title and ">" not in c.youtube_description


def test_youtube_description_is_full_cleaned_caption():
    raw = "Line one. Line two.\n#cats #fyp"
    assert captions.youtube_description(raw, CFG) == "Line one. Line two.\n#cats"


def test_custom_strip_list():
    cfg = CaptionConfig(strip_tags=("cats",))
    assert captions.strip_tags("hi #cats #fyp", cfg.strip_tags) == "hi #fyp"

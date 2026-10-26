from crosspost.clients.google import DriveFile
from crosspost.clients.tiktok import TikTokVideo
from crosspost.config import Config, RetryConfig
from crosspost.job import Clients, Job
from crosspost.media import Probe
from crosspost.state import BACKFILL, DONE, FAILED, PENDING, State, Video

from .fakes import Clock, FakeDrive, FakeInstagram, FakeTikTok, FakeYouTube, fake_probe

T0 = 1_800_000_000


def tt(id, t, dur=15, caption="Sunset run! #fyp #running"):
    return TikTokVideo(id=id, create_time=t, caption=caption, duration=dur, share_url=f"https://tiktok/{id}")


def df(id, t, dur=15.2, name=None):
    return DriveFile(id=id, name=name or f"{id}.mov", created_time=t, duration=dur)


def make(videos=(), files=(), ig=None, yt=None, state=None, cfg=None, dry_run=False, probes=None):
    clock = Clock(T0)
    state = state or State(watermark=T0 - 3600)
    saves = []
    drive = FakeDrive(files)
    clients = Clients(
        tiktok=FakeTikTok(videos),
        drive=drive,
        instagram=ig if ig is not None else FakeInstagram(),
        youtube=yt if yt is not None else FakeYouTube(),
        probe=fake_probe(probes or {}),
    )
    job = Job(cfg or Config(), state, clients, lambda: saves.append(1), dry_run=dry_run, now=clock)
    return job, state, clients, clock, saves


def test_happy_path_publishes_both_and_archives():
    job, state, c, _, saves = make([tt("1", T0 - 600)], [df("f1", T0 - 590)])
    assert job.run() == 0
    v = state.videos["1"]
    assert v.drive_file_id == "f1"
    assert v.platforms["instagram"].status == DONE and v.platforms["instagram"].post_id == "ig1"
    assert v.platforms["youtube"].status == DONE
    assert c.instagram.published == ["Sunset run! #running"]
    assert c.youtube.uploads == [("Sunset run! #Shorts", "Sunset run! #running", "private")]
    assert c.drive.parent["f1"] == "done" and v.archived
    assert saves  # progress was checkpointed


def test_nothing_new_exits_zero():
    job, state, *_ = make()
    assert job.run() == 0
    assert state.videos == {}


def test_first_run_sets_watermark_and_ignores_old_posts():
    state = State()
    job, state, *_ = make([tt("old", T0 - 86400), tt("new", T0 - 60)], state=state)
    job.run()
    assert state.watermark == T0 - 3600
    assert set(state.videos) == {"new"}


def test_unmatched_drive_files_never_posted():
    job, state, c, *_ = make([], [df("stray", T0)])
    assert job.run() == 0
    assert c.instagram.published == [] and c.drive.parent["stray"] == "inbox"


def test_unmatched_post_retries_then_flags_and_fails_once():
    job, state, c, clock, _ = make([tt("1", T0 - 60)], [df("f1", T0, dur=40)])
    assert job.run() == 0
    assert not state.videos["1"].flagged

    clock.t += 25 * 3600
    job2 = Job(Config(), state, c, lambda: None, now=clock)
    assert job2.run() == 1
    assert state.videos["1"].flagged

    job3 = Job(Config(), state, c, lambda: None, now=clock)
    assert job3.run() == 0  # flagged once, not on every run after


def test_one_platform_failing_does_not_block_other_and_retries():
    job, state, c, clock, _ = make([tt("1", T0 - 60)], [df("f1", T0)], ig=FakeInstagram(fail_times=1))
    assert job.run() == 0  # retryable failure is not a run failure
    v = state.videos["1"]
    assert v.platforms["youtube"].status == DONE
    assert v.platforms["instagram"].status == PENDING and v.platforms["instagram"].attempts == 1
    assert "ig boom" in v.platforms["instagram"].last_error
    assert not v.archived  # still has work to do

    Job(Config(), state, c, lambda: None, now=clock).run()
    assert v.platforms["instagram"].status == DONE
    assert len(c.youtube.uploads) == 1  # not re-uploaded
    assert v.archived


def test_gives_up_after_max_attempts_and_exits_nonzero():
    cfg = Config(retries=RetryConfig(max_attempts=2))
    job, state, c, clock, _ = make([tt("1", T0 - 60)], [df("f1", T0)], yt=FakeYouTube(fail_times=5), cfg=cfg)
    assert job.run() == 0
    assert Job(cfg, state, c, lambda: None, now=clock).run() == 1
    v = state.videos["1"]
    assert v.platforms["youtube"].status == FAILED
    assert v.archived  # nothing left to retry, so the file is moved on


def test_instagram_limit_defers_without_counting_attempt():
    job, state, c, *_ = make([tt("1", T0 - 60)], [df("f1", T0)], ig=FakeInstagram(quota=(100, 100)))
    assert job.run() == 0
    s = state.videos["1"].platforms["instagram"]
    assert s.status == PENDING and s.attempts == 0


def test_youtube_quota_exceeded_stops_further_uploads_today():
    vids = [tt("1", T0 - 120), tt("2", T0 - 60)]
    files = [df("f1", T0 - 120), df("f2", T0 - 60)]
    yt = FakeYouTube(quota_exceeded=True)
    job, state, *_ = make(vids, files, yt=yt)
    assert job.run() == 0
    assert state.count("youtube_uploads", T0) == Config().youtube.daily_upload_limit
    assert all(v.platforms["youtube"].attempts == 0 for v in state.videos.values())


def test_dry_run_publishes_nothing_and_moves_nothing():
    job, state, c, _, saves = make([tt("1", T0 - 60)], [df("f1", T0)], dry_run=True)
    assert job.run() == 0
    assert c.instagram.published == [] and c.youtube.uploads == []
    assert c.drive.parent["f1"] == "inbox"
    assert state.videos["1"].platforms["youtube"].status == PENDING


def test_probes_file_when_drive_lacks_duration_and_caches():
    files = [df("f1", T0 + 5000, dur=None)]  # Drive createdTime far off; ffprobe time is right
    probes = {"f1": Probe(duration=15.3, time=T0 - 50)}
    job, state, c, *_ = make([tt("1", T0 - 60)], files, probes=probes, ig=FakeInstagram(quota=(1, 1)))
    job.run()
    assert state.videos["1"].drive_file_id == "f1"
    assert c.drive.downloads == ["f1"]  # downloaded once, reused for the upload


def test_filename_timestamp_beats_drive_created_time():
    # Shortcut name says 16:15+00:00 == T0-60-ish; Drive's createdTime is hours later.
    from crosspost.media import time_from_name
    name = "tt-20270115T075900+0000.mov"
    assert time_from_name(name) == T0 - 60
    job, state, *_ = make([tt("1", T0 - 60)], [df("f1", T0 + 7200, name=name)])
    job.run()
    assert state.videos["1"].drive_file_id == "f1"


def test_backfill_queue_drains_oldest_first_within_daily_cap():
    state = State(watermark=T0)
    for i, t in enumerate([300, 100, 200]):
        state.add_video(Video(id=f"b{i}", create_time=t, caption=f"old {i}", duration=10,
                                  source=BACKFILL, drive_file_id=f"bf{i}"), ["instagram", "youtube"])
    files = [df(f"bf{i}", 0) for i in range(3)]
    job, state, c, clock, _ = make(state=state, files=files)
    job.run()
    # default caps: instagram 5/day, youtube 3/day -> all IG; YouTube all 3 too
    assert c.instagram.published == ["old 1", "old 2", "old 0"]

    cfg = Config()
    from dataclasses import replace
    cfg = replace(cfg, backfill=replace(cfg.backfill, max_per_day={"instagram": 1, "youtube": 0}))
    state2 = State(watermark=T0)
    for i, t in enumerate([300, 100]):
        state2.add_video(Video(id=f"b{i}", create_time=t, caption=f"old {i}", duration=10,
                               source=BACKFILL, drive_file_id=f"bf{i}"), ["instagram", "youtube"])
    job, state2, c, clock, _ = make(state=state2, files=files, cfg=cfg)
    job.run()
    assert c.instagram.published == ["old 1"]
    assert c.youtube.uploads == []


def test_live_posts_go_before_backfill():
    state = State(watermark=T0 - 3600)
    state.add_video(Video(id="old", create_time=5, caption="old", duration=10, source=BACKFILL,
                          drive_file_id="bf"), ["instagram"])
    job, state, c, *_ = make([tt("1", T0 - 60, caption="new")], [df("f1", T0), df("bf", 0, dur=99)],
                             state=state)
    job.run()
    assert c.instagram.published == ["new", "old"]

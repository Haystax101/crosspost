"""The scheduled pipeline, independent of how clients/state are built (so it's testable).

Steps per run:
  1. pull new TikToks (created after the state watermark) into state
  2. match unmatched live posts to inbox files by time AND duration
  3. publish each matched video to every pending platform, independently
  4. move fully-settled files to the done folder
"""

from __future__ import annotations

import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import captions, log, matching
from .clients import Deferred
from .config import Config
from .media import Probe, time_from_name
from .state import BACKFILL, DONE, FAILED, LIVE, State, Video

# On the very first run, look back this far so a post made just before setup isn't lost.
FIRST_RUN_LOOKBACK_S = 3600


@dataclass
class Clients:
    tiktok: object
    drive: object
    instagram: object | None
    youtube: object | None
    probe: Callable[[Path], Probe]


class Job:
    def __init__(
        self,
        cfg: Config,
        state: State,
        clients: Clients,
        checkpoint: Callable[[], None],
        dry_run: bool = False,
        now: Callable[[], float] = time.time,
    ):
        self.cfg = cfg
        self.state = state
        self.c = clients
        self.checkpoint = checkpoint
        self.dry_run = dry_run
        self.now = now
        self.failures = 0
        self._workdir = Path(tempfile.mkdtemp(prefix="crosspost-"))
        self._local: dict[str, Path] = {}
        self._folders: dict[str, str] = {}

    # --- entry point -----------------------------------------------------

    def run(self) -> int:
        try:
            if self.c.instagram is not None:
                self.c.instagram.ensure_fresh(self.cfg.instagram.refresh_days_before_expiry)
            self.discover()
            self.match()
            self.publish_all()
            self.archive()
        finally:
            shutil.rmtree(self._workdir, ignore_errors=True)
        log.info("run finished", failures=self.failures, dry_run=self.dry_run)
        return 1 if self.failures else 0

    # --- 1. discover -----------------------------------------------------

    def discover(self) -> None:
        if self.state.watermark is None:
            self.state.watermark = int(self.now()) - FIRST_RUN_LOOKBACK_S
            log.info("first run: only TikToks from now on are picked up; use backfill for older ones",
                     watermark=self.state.watermark)
        new = 0
        for v in self.c.tiktok.iter_videos(since=self.state.watermark):
            if v.id in self.state.videos:
                continue
            self.state.add_video(
                Video(id=v.id, create_time=v.create_time, caption=v.caption, duration=v.duration,
                      share_url=v.share_url, source=LIVE, first_seen=int(self.now())),
                self.cfg.enabled_platforms(),
            )
            log.info("new tiktok", tiktok_id=v.id, create_time=v.create_time, duration=v.duration)
            new += 1
        if new:
            self.checkpoint()
        else:
            log.info("no new tiktoks")

    # --- 2. match --------------------------------------------------------

    def _folder(self, key: str) -> str:
        if key not in self._folders:
            d = self.cfg.drive
            path = {"inbox": d.inbox_folder, "done": f"{d.inbox_folder}/{d.done_folder}"}[key]
            self._folders[key] = self.c.drive.folder_id(path, create=(key == "done"))
        return self._folders[key]

    def _local_file(self, file_id: str) -> Path:
        if file_id not in self._local:
            self._local[file_id] = self.c.drive.download(file_id, self._workdir / f"{file_id}.mp4")
        return self._local[file_id]

    def _file_info(self, f) -> matching.FileInfo:
        """Best-known time and duration for an inbox file, probing (and caching) only when needed."""
        cached = self.state.probes.get(f.id)
        if cached is None and (f.duration is None or time_from_name(f.name) is None):
            try:
                p = self.c.probe(self._local_file(f.id))
                cached = {"duration": p.duration, "time": p.time}
            except Exception as e:  # a corrupt/odd file must not break matching for the rest
                log.warning("probe failed", file=f.name, error=e)
                cached = {"duration": None, "time": None}
            self.state.probes[f.id] = cached
        cached = cached or {}
        when = time_from_name(f.name) or cached.get("time") or f.created_time
        return matching.FileInfo(f.id, when, f.duration if f.duration is not None else cached.get("duration"))

    def match(self) -> None:
        waiting = [v for v in self.state.videos.values()
                   if v.source == LIVE and v.drive_file_id is None and not v.flagged and not v.settled()]
        if not waiting:
            return
        taken = {v.drive_file_id for v in self.state.videos.values() if v.drive_file_id}
        files = [f for f in self.c.drive.list_videos(self._folder("inbox")) if f.id not in taken]
        live_ids = {f.id for f in files}
        self.state.probes = {k: v for k, v in self.state.probes.items() if k in live_ids}
        infos = [self._file_info(f) for f in files]
        names = {f.id: f.name for f in files}

        m = self.cfg.matching
        found = matching.match(
            [matching.Post(v.id, v.create_time, v.duration) for v in waiting],
            infos, m.window_minutes * 60, m.duration_tolerance_s,
        )
        for v in waiting:
            if v.id in found:
                v.drive_file_id = found[v.id]
                log.info("matched", tiktok_id=v.id, file=names[found[v.id]])
            elif self.now() - v.first_seen > m.unmatched_retry_hours * 3600:
                v.flagged = True
                self.failures += 1
                log.error("no inbox file matched within retry window; giving up on this post",
                          tiktok_id=v.id, share_url=v.share_url, hours=m.unmatched_retry_hours)
            else:
                log.info("not matched yet; will retry", tiktok_id=v.id,
                         waited_min=int((self.now() - v.first_seen) / 60))
        unmatched = [names[i.id] for i in infos if i.id not in found.values()]
        if unmatched:
            log.debug("inbox files with no matching post (ignored)", files=",".join(unmatched))
        self.checkpoint()

    # --- 3. publish ------------------------------------------------------

    def publish_all(self) -> None:
        ready = [v for v in self.state.videos.values() if v.drive_file_id and not v.settled()]
        # Live posts first, then the backfill queue oldest first.
        ready.sort(key=lambda v: (v.source != LIVE, v.create_time))
        for video in ready:
            for platform in video.pending_platforms():
                self.publish(video, platform)

    def _gate(self, video: Video, platform: str) -> str | None:
        """Return a reason to hold this upload for a later run, or None to go ahead."""
        now = self.now()
        if platform == "youtube":
            limit = self.cfg.youtube.daily_upload_limit
            if self.state.count("youtube_uploads", now) >= limit:
                return f"YouTube daily upload limit reached ({limit})"
        if video.source == BACKFILL:
            cap = self.cfg.backfill.max_per_day.get(platform, 0)
            if self.state.count(f"backfill_{platform}", now) >= cap:
                return f"backfill daily cap reached for {platform} ({cap})"
        return None

    def publish(self, video: Video, platform: str) -> None:
        status = video.platforms[platform]
        fields = dict(tiktok_id=video.id, platform=platform, source=video.source)
        reason = self._gate(video, platform)
        if reason:
            log.info("deferred", reason=reason, **fields)
            return
        caps = captions.transform(video.caption, self.cfg.captions)
        if self.dry_run:
            if platform == "instagram":
                log.info("dry-run: would publish", caption=caps.instagram, **fields)
            else:
                log.info("dry-run: would publish", title=caps.youtube_title,
                         privacy=self.cfg.youtube.privacy_status, **fields)
            return
        try:
            path = self._local_file(video.drive_file_id)
            if platform == "instagram":
                ig = self.cfg.instagram
                post_id = self.c.instagram.publish_reel(path, caps.instagram, ig.share_to_feed,
                                                        ig.poll_interval_s, ig.poll_timeout_s)
            else:
                yt = self.cfg.youtube
                # Count the attempt up front: a failed insert still spends quota.
                self.state.bump("youtube_uploads", self.now())
                post_id = self.c.youtube.upload(path, caps.youtube_title, caps.youtube_description,
                                                yt.privacy_status, yt.category_id, yt.made_for_kids)
        except Deferred as e:
            if platform == "youtube":  # out of quota: don't try again until the Pacific-midnight reset
                self.state.counters["youtube_uploads"] = {"day": self.state.day(self.now()),
                                                          "count": self.cfg.youtube.daily_upload_limit}
            log.info("deferred", reason=str(e), **fields)
            return
        except Exception as e:
            status.attempts += 1
            status.last_error = str(e)[:500]
            status.updated_at = int(self.now())
            if status.attempts >= self.cfg.retries.max_attempts:
                status.status = FAILED
                self.failures += 1
                log.error("publish failed; giving up", attempts=status.attempts, error=e, **fields)
            else:
                log.warning("publish failed; will retry", attempts=status.attempts, error=e, **fields)
            self.checkpoint()
            return
        status.status, status.post_id, status.last_error = DONE, post_id, None
        status.attempts += 1
        status.updated_at = int(self.now())
        if video.source == BACKFILL:
            self.state.bump(f"backfill_{platform}", self.now())
        log.info("published", post_id=post_id, **fields)
        # Save straight away so a crash later in the run can't cause a double post.
        self.checkpoint()

    # --- 4. archive ------------------------------------------------------

    def archive(self) -> None:
        for v in self.state.videos.values():
            if v.drive_file_id and v.settled() and not v.archived:
                if self.dry_run:
                    log.info("dry-run: would move to done", tiktok_id=v.id)
                    continue
                self.c.drive.move(v.drive_file_id, self._folder("done"))
                v.archived = True
                log.info("moved to done", tiktok_id=v.id,
                         **{p: s.status for p, s in v.platforms.items()})
                self.checkpoint()

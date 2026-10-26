"""In-memory stand-ins for the API clients. No network anywhere in the test suite."""

from __future__ import annotations

from pathlib import Path

from crosspost.clients import ApiError, Deferred
from crosspost.clients.google import DriveFile
from crosspost.clients.tiktok import TikTokVideo
from crosspost.media import Probe


class Clock:
    def __init__(self, t: float = 1_800_000_000):
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeTikTok:
    def __init__(self, videos: list[TikTokVideo] = ()):
        self.videos = list(videos)

    def iter_videos(self, since=None):
        for v in sorted(self.videos, key=lambda v: -v.create_time):
            if since is not None and v.create_time < since:
                return
            yield v


class FakeDrive:
    def __init__(self, files: list[DriveFile] = ()):
        self.folders = {"tiktok-inbox": "inbox", "tiktok-inbox/done": "done"}
        self.files = {f.id: f for f in files}
        self.parent = {f.id: "inbox" for f in files}
        self.downloads: list[str] = []

    def folder_id(self, path, create=False):
        return self.folders[path]

    def list_videos(self, folder_id):
        return [f for i, f in self.files.items() if self.parent[i] == folder_id]

    def download(self, file_id, dest: Path):
        self.downloads.append(file_id)
        dest.write_bytes(b"video")
        return dest

    def move(self, file_id, folder_id):
        self.parent[file_id] = folder_id


class FakeInstagram:
    def __init__(self, fail_times: int = 0, quota: tuple[int, int] = (0, 100)):
        self.fail_times = fail_times
        self.quota = quota
        self.published: list[str] = []
        self.refreshed = False

    def ensure_fresh(self, days):
        return False

    def publish_reel(self, path, caption, share_to_feed, poll_interval_s, poll_timeout_s):
        used, total = self.quota
        if used >= total:
            raise Deferred("limit")
        if self.fail_times:
            self.fail_times -= 1
            raise ApiError("ig boom")
        self.published.append(caption)
        self.quota = (used + 1, total)
        return f"ig{len(self.published)}"


class FakeYouTube:
    def __init__(self, fail_times: int = 0, quota_exceeded: bool = False):
        self.fail_times = fail_times
        self.quota_exceeded = quota_exceeded
        self.uploads: list[tuple[str, str, str]] = []

    def upload(self, path, title, description, privacy_status, category_id, made_for_kids):
        if self.quota_exceeded:
            raise Deferred("quotaExceeded")
        if self.fail_times:
            self.fail_times -= 1
            raise ApiError("yt boom")
        self.uploads.append((title, description, privacy_status))
        return f"yt{len(self.uploads)}"


def fake_probe(results: dict[str, Probe]):
    """Probe keyed by the downloaded file's stem (= Drive file id)."""
    def probe(path: Path) -> Probe:
        return results[path.stem]
    return probe

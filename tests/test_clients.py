"""API clients against a scripted fake HTTP session (no network)."""

import pytest

from crosspost.clients import ApiError, Deferred
from crosspost.clients.instagram import Instagram
from crosspost.clients.tiktok import TikTok

from .fakes import Clock


class Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.text = data, status, str(data)

    def json(self):
        return self._data


class Session:
    """Replays queued responses and records each request as (method, url, kwargs)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, kw):
        if "data" in kw and hasattr(kw["data"], "read"):
            kw = {**kw, "data": kw["data"].read()}
        self.calls.append((method, url, kw))
        return self.responses.pop(0)

    def get(self, url, **kw):
        return self._next("GET", url, kw)

    def post(self, url, **kw):
        return self._next("POST", url, kw)


T0 = 1_800_000_000


def tiktok_tokens(expires_at):
    return {"access_token": "old", "expires_at": expires_at, "refresh_token": "r1",
            "refresh_expires_at": T0 + 10**7, "open_id": "o"}


def page(videos, has_more=False, cursor=None):
    return Resp({"data": {"videos": videos, "has_more": has_more, "cursor": cursor},
                 "error": {"code": "ok", "message": ""}})


def v(id, t):
    return {"id": id, "create_time": t, "video_description": f"cap {id}", "title": "", "duration": 12,
            "share_url": f"u{id}"}


def test_tiktok_refreshes_expired_token_persists_rotation_and_paginates():
    saved = []
    s = Session(
        Resp({"access_token": "new", "expires_in": 86400, "refresh_token": "r2",
              "refresh_expires_in": 31536000, "open_id": "o"}),
        page([v("3", T0 - 10), v("2", T0 - 20)], has_more=True, cursor=123),
        page([v("1", T0 - 30), v("0", T0 - 9999)], has_more=True, cursor=456),
    )
    tt = TikTok("k", "s", tiktok_tokens(T0 - 1), saved.append, session=s, now=Clock(T0))
    got = [x.id for x in tt.iter_videos(since=T0 - 100)]
    assert got == ["3", "2", "1"]  # stops at the first older-than-since video
    assert saved[0]["refresh_token"] == "r2" and saved[0]["access_token"] == "new"
    assert s.calls[1][2]["headers"]["Authorization"] == "Bearer new"
    assert s.calls[2][2]["json"] == {"max_count": 20, "cursor": 123}
    assert len(s.calls) == 3  # didn't fetch a third page


def test_tiktok_api_error_raises():
    s = Session(Resp({"data": {}, "error": {"code": "access_token_invalid", "message": "bad"}}, 401))
    tt = TikTok("k", "s", tiktok_tokens(T0 + 9999), lambda t: None, session=s, now=Clock(T0))
    with pytest.raises(ApiError, match="access_token_invalid"):
        list(tt.iter_videos())


def ig_tokens(**kw):
    return {"access_token": "tok", "expires_at": T0 + 50 * 86400, "issued_at": T0 - 10 * 86400, "user_id": "42", **kw}


def test_instagram_publish_reel_full_flow(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x" * 10)
    sleeps = []
    s = Session(
        Resp({"data": [{"quota_usage": 3, "config": {"quota_total": 100}}]}),
        Resp({"id": "C1", "uri": "https://rupload.facebook.com/ig-api-upload/v24.0/C1"}),
        Resp({"success": True}),
        Resp({"status_code": "IN_PROGRESS"}),
        Resp({"status_code": "FINISHED"}),
        Resp({"id": "M1"}),
    )
    ig = Instagram(ig_tokens(), lambda t: None, "v24.0", session=s, now=Clock(T0), sleep=sleeps.append)
    assert ig.publish_reel(f, "hello", True, 5, 600) == "M1"

    (_, url, kw) = s.calls[1]
    assert url.endswith("/v24.0/42/media")
    assert kw["data"]["media_type"] == "REELS" and kw["data"]["upload_type"] == "resumable"
    assert "video_url" not in kw["data"]
    (_, url, kw) = s.calls[2]
    assert url == "https://rupload.facebook.com/ig-api-upload/v24.0/C1"
    assert kw["headers"] == {"Authorization": "OAuth tok", "offset": "0", "file_size": "10"}
    assert kw["data"] == b"x" * 10
    assert sleeps == [5]
    assert s.calls[5][2]["data"]["creation_id"] == "C1"


def test_instagram_defers_at_publishing_limit(tmp_path):
    s = Session(Resp({"data": [{"quota_usage": 100, "config": {"quota_total": 100}}]}))
    ig = Instagram(ig_tokens(), lambda t: None, "v24.0", session=s, now=Clock(T0))
    with pytest.raises(Deferred):
        ig.publish_reel(tmp_path / "missing.mp4", "c", True, 1, 1)
    assert len(s.calls) == 1  # nothing created


def test_instagram_container_error_raises(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    s = Session(
        Resp({"data": [{"quota_usage": 0, "config": {"quota_total": 100}}]}),
        Resp({"id": "C1"}), Resp({"success": True}),
        Resp({"status_code": "ERROR", "status": "Error: unsupported codec"}),
    )
    ig = Instagram(ig_tokens(), lambda t: None, "v24.0", session=s, now=Clock(T0), sleep=lambda s: None)
    with pytest.raises(ApiError, match="unsupported codec"):
        ig.publish_reel(f, "c", True, 1, 600)


def test_instagram_graph_error_raises():
    s = Session(Resp({"error": {"message": "Invalid OAuth access token", "code": 190}}, 400))
    ig = Instagram(ig_tokens(), lambda t: None, "v24.0", session=s, now=Clock(T0))
    with pytest.raises(ApiError, match="Invalid OAuth"):
        ig.publishing_quota()


def test_instagram_token_refresh_only_when_near_expiry():
    saved = []
    ig = Instagram(ig_tokens(), saved.append, "v24.0", session=Session(), now=Clock(T0))
    assert ig.ensure_fresh(15) is False  # 50 days left

    s = Session(Resp({"access_token": "tok2", "expires_in": 60 * 86400}))
    ig = Instagram(ig_tokens(expires_at=T0 + 5 * 86400), saved.append, "v24.0", session=s, now=Clock(T0))
    assert ig.ensure_fresh(15) is True
    assert saved[-1]["access_token"] == "tok2" and saved[-1]["expires_at"] == T0 + 60 * 86400
    assert saved[-1]["user_id"] == "42"


def test_instagram_expired_token_is_a_hard_error():
    ig = Instagram(ig_tokens(expires_at=T0 - 1), lambda t: None, "v24.0", session=Session(), now=Clock(T0))
    with pytest.raises(ApiError, match="auth instagram"):
        ig.ensure_fresh(15)


def test_instagram_iter_media_follows_paging():
    s = Session(
        Resp({"data": [{"id": "1"}], "paging": {"next": "https://next"}}),
        Resp({"data": [{"id": "2"}], "paging": {}}),
    )
    ig = Instagram(ig_tokens(), lambda t: None, "v24.0", session=s, now=Clock(T0))
    assert [m["id"] for m in ig.iter_media()] == ["1", "2"]
    assert s.calls[1][1] == "https://next" and s.calls[1][2]["params"] is None

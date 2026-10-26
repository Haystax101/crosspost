"""TikTok Login Kit + Display API v2 (scopes user.info.basic, video.list).

The Display API returns metadata only; the video files come from Drive.
Access tokens last ~24h; refresh tokens rotate on every refresh, so every
refresh is persisted immediately via `on_tokens`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from urllib.parse import urlencode

import requests

from . import ApiError

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
VIDEO_LIST_URL = "https://open.tiktokapis.com/v2/video/list/"
SCOPES = "user.info.basic,video.list"
FIELDS = "id,create_time,video_description,title,duration,share_url"


@dataclass(frozen=True)
class TikTokVideo:
    id: str
    create_time: int
    caption: str
    duration: float | None
    share_url: str


def authorize_url(client_key: str, redirect_uri: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode(
        {
            "client_key": client_key,
            "scope": SCOPES,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )


def _tokens_from(resp: requests.Response, now: float) -> dict:
    data = resp.json()
    if "access_token" not in data:
        raise ApiError(f"TikTok token request failed: {data.get('error')}: {data.get('error_description')}")
    return {
        "access_token": data["access_token"],
        "expires_at": int(now + data["expires_in"]),
        "refresh_token": data["refresh_token"],
        "refresh_expires_at": int(now + data["refresh_expires_in"]),
        "open_id": data.get("open_id"),
    }


def exchange_code(client_key: str, client_secret: str, code: str, redirect_uri: str, session=None) -> dict:
    resp = (session or requests).post(
        TOKEN_URL,
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    return _tokens_from(resp, time.time())


class TikTok:
    def __init__(
        self,
        client_key: str,
        client_secret: str,
        tokens: dict,
        on_tokens: Callable[[dict], None],
        session: requests.Session | None = None,
        now: Callable[[], float] = time.time,
    ):
        if not tokens:
            raise ApiError("no TikTok tokens in state: run `python -m crosspost.auth tiktok`")
        self.client_key = client_key
        self.client_secret = client_secret
        self.tokens = tokens
        self.on_tokens = on_tokens
        self.session = session or requests.Session()
        self.now = now

    def refresh(self) -> None:
        if self.tokens["refresh_expires_at"] <= self.now():
            raise ApiError("TikTok refresh token expired: run `python -m crosspost.auth tiktok`")
        resp = self.session.post(
            TOKEN_URL,
            data={
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.tokens["refresh_token"],
            },
            timeout=30,
        )
        self.tokens = _tokens_from(resp, self.now())
        self.on_tokens(self.tokens)

    def _access_token(self) -> str:
        if self.tokens["expires_at"] - 300 <= self.now():
            self.refresh()
        return self.tokens["access_token"]

    def iter_videos(self, since: int | None = None, page_size: int = 20) -> Iterator[TikTokVideo]:
        """Yield the user's videos newest first, stopping before `since` (unix seconds)."""
        cursor = None
        while True:
            body: dict = {"max_count": page_size}
            if cursor is not None:
                body["cursor"] = cursor
            resp = self.session.post(
                VIDEO_LIST_URL,
                params={"fields": FIELDS},
                json=body,
                headers={"Authorization": f"Bearer {self._access_token()}"},
                timeout=30,
            )
            payload = resp.json()
            err = payload.get("error", {})
            if err.get("code") != "ok":
                raise ApiError(f"TikTok video.list failed ({resp.status_code}): {err.get('code')}: {err.get('message')}")
            data = payload.get("data", {})
            for v in data.get("videos", []):
                if since is not None and v["create_time"] < since:
                    return
                yield TikTokVideo(
                    id=str(v["id"]),
                    create_time=int(v["create_time"]),
                    caption=v.get("video_description") or v.get("title") or "",
                    duration=float(v["duration"]) if v.get("duration") is not None else None,
                    share_url=v.get("share_url", ""),
                )
            if not data.get("has_more"):
                return
            cursor = data.get("cursor")

"""Instagram API with Instagram Login (graph.instagram.com), professional account.

Reels are published with the resumable upload host (rupload.facebook.com) from a
local file, never via video_url. Flow: create REELS container -> upload bytes ->
poll status_code until FINISHED -> media_publish. The 24h publishing limit is
checked before each publish.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import urlencode

import requests

from . import ApiError, Deferred

AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
SHORT_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH = "https://graph.instagram.com"
RUPLOAD = "https://rupload.facebook.com/ig-api-upload"
SCOPES = "instagram_business_basic,instagram_business_content_publish"


def authorize_url(app_id: str, redirect_uri: str, state: str) -> str:
    return AUTHORIZE_URL + "?" + urlencode(
        {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
        }
    )


def _json(resp: requests.Response, what: str) -> dict:
    try:
        data = resp.json()
    except ValueError:
        raise ApiError(f"Instagram {what} failed ({resp.status_code}): {resp.text[:300]}") from None
    if resp.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        msg = err.get("message", data) if isinstance(err, dict) else err
        raise ApiError(f"Instagram {what} failed ({resp.status_code}): {msg}")
    return data


def exchange_code(app_id: str, app_secret: str, redirect_uri: str, code: str, session=None) -> dict:
    """Code -> short-lived token -> 60-day long-lived token."""
    s = session or requests
    short = _json(
        s.post(
            SHORT_TOKEN_URL,
            data={
                "client_id": app_id,
                "client_secret": app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code.removesuffix("#_"),
            },
            timeout=30,
        ),
        "code exchange",
    )
    long = _json(
        s.get(
            f"{GRAPH}/access_token",
            params={"grant_type": "ig_exchange_token", "client_secret": app_secret, "access_token": short["access_token"]},
            timeout=30,
        ),
        "long-lived token exchange",
    )
    now = int(time.time())
    return {
        "access_token": long["access_token"],
        "expires_at": now + int(long["expires_in"]),
        "issued_at": now,
        "user_id": str(short["user_id"]),
    }


class Instagram:
    def __init__(
        self,
        tokens: dict,
        on_tokens: Callable[[dict], None],
        api_version: str,
        session: requests.Session | None = None,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not tokens:
            raise ApiError("no Instagram token in state: run `python -m crosspost.auth instagram`")
        self.tokens = tokens
        self.on_tokens = on_tokens
        self.base = f"{GRAPH}/{api_version}"
        self.api_version = api_version
        self.session = session or requests.Session()
        self.now = now
        self.sleep = sleep

    @property
    def _token(self) -> str:
        return self.tokens["access_token"]

    @property
    def user_id(self) -> str:
        return self.tokens["user_id"]

    def ensure_fresh(self, days_before_expiry: float) -> bool:
        """Refresh the long-lived token if it expires soon. Tokens must be >24h old to refresh."""
        now = self.now()
        if self.tokens["expires_at"] <= now:
            raise ApiError("Instagram token expired: run `python -m crosspost.auth instagram`")
        if self.tokens["expires_at"] - now > days_before_expiry * 86400 or now - self.tokens["issued_at"] < 86400:
            return False
        data = _json(
            self.session.get(
                f"{GRAPH}/refresh_access_token",
                params={"grant_type": "ig_refresh_token", "access_token": self._token},
                timeout=30,
            ),
            "token refresh",
        )
        self.tokens = {**self.tokens, "access_token": data["access_token"], "expires_at": int(now + data["expires_in"]), "issued_at": int(now)}
        self.on_tokens(self.tokens)
        return True

    def publishing_quota(self) -> tuple[int, int]:
        """(posts used, posts allowed) in the current rolling 24h window."""
        data = _json(
            self.session.get(
                f"{self.base}/{self.user_id}/content_publishing_limit",
                params={"fields": "quota_usage,config", "access_token": self._token},
                timeout=30,
            ),
            "content_publishing_limit",
        )
        entry = data["data"][0]
        return int(entry.get("quota_usage", 0)), int(entry["config"]["quota_total"])

    def publish_reel(
        self, path: Path, caption: str, share_to_feed: bool, poll_interval_s: float, poll_timeout_s: float
    ) -> str:
        used, total = self.publishing_quota()
        if used >= total:
            raise Deferred(f"Instagram publishing limit reached ({used}/{total} in 24h)")

        container = _json(
            self.session.post(
                f"{self.base}/{self.user_id}/media",
                data={
                    "media_type": "REELS",
                    "upload_type": "resumable",
                    "caption": caption,
                    "share_to_feed": "true" if share_to_feed else "false",
                    "access_token": self._token,
                },
                timeout=30,
            ),
            "create container",
        )["id"]

        size = os.path.getsize(path)
        with open(path, "rb") as f:
            _json(
                self.session.post(
                    f"{RUPLOAD}/{self.api_version}/{container}",
                    headers={"Authorization": f"OAuth {self._token}", "offset": "0", "file_size": str(size)},
                    data=f,
                    timeout=600,
                ),
                "rupload",
            )

        deadline = self.now() + poll_timeout_s
        while True:
            status = _json(
                self.session.get(
                    f"{self.base}/{container}",
                    params={"fields": "status_code,status", "access_token": self._token},
                    timeout=30,
                ),
                "container status",
            )
            code = status.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise ApiError(f"Instagram container {container} {code}: {status.get('status')}")
            if self.now() >= deadline:
                raise ApiError(f"Instagram container {container} still {code} after {poll_timeout_s}s")
            self.sleep(poll_interval_s)

        return _json(
            self.session.post(
                f"{self.base}/{self.user_id}/media_publish",
                data={"creation_id": container, "access_token": self._token},
                timeout=60,
            ),
            "media_publish",
        )["id"]

    def iter_media(self) -> Iterator[dict]:
        """All of the account's media: id, caption, timestamp, permalink."""
        url = f"{self.base}/{self.user_id}/media"
        params: dict | None = {"fields": "id,caption,timestamp,permalink,media_type", "limit": 100, "access_token": self._token}
        while url:
            data = _json(self.session.get(url, params=params, timeout=30), "list media")
            yield from data.get("data", [])
            url, params = data.get("paging", {}).get("next"), None

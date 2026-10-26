"""One-off local OAuth setup. Run these in order:

    python -m crosspost.auth keygen      # state encryption key -> .env (and a GitHub Secret)
    python -m crosspost.auth google      # YouTube + Drive refresh token -> .env (and a GitHub Secret)
    python -m crosspost.auth tiktok      # TikTok tokens -> encrypted state in Drive
    python -m crosspost.auth instagram   # Instagram long-lived token -> encrypted state in Drive

TikTok and Instagram need an https redirect URI, so instead of a local server you
approve in the browser, land on the callback page (see docs/callback.html), and
paste the URL back here.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from . import app, config
from .clients import google, instagram, tiktok
from .store import generate_key

DEFAULT_REDIRECT = "https://haystax101.github.io/crosspost/docs/callback.html"


def _paste_code(url: str, expected_state: str) -> str:
    print("\n1. Open this URL, log in and approve:\n\n   " + url + "\n")
    pasted = input("2. Paste the full URL you were redirected to: ").strip()
    params = parse_qs(urlparse(pasted).query)
    if params.get("state", [None])[0] != expected_state:
        raise SystemExit("state mismatch: paste the URL from this attempt")
    if "code" not in params:
        raise SystemExit(f"no code in URL (error: {params.get('error_description') or params.get('error')})")
    return params["code"][0]


def _save_tokens(platform: str, tokens: dict) -> None:
    cfg = config.load()
    store = app.store(cfg)
    state = store.load()
    state.tokens[platform] = tokens
    store.save(state)
    expires = datetime.fromtimestamp(tokens["expires_at"], timezone.utc).isoformat()
    print(f"\nSaved {platform} tokens to the encrypted state in Drive (access token expires {expires}).")


def keygen() -> None:
    key = generate_key()
    app.set_dotenv("CROSSPOST_STATE_KEY", key)
    print("CROSSPOST_STATE_KEY written to .env. Add the same value as a GitHub Secret:\n\n" + key)


def google_auth() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(
        {"installed": {
            "client_id": app.env("GOOGLE_CLIENT_ID"),
            "client_secret": app.env("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": google.TOKEN_URI,
            "redirect_uris": ["http://localhost"],
        }},
        google.SCOPES,
    )
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        raise SystemExit("Google returned no refresh token; remove the app's access at myaccount.google.com/permissions and retry")
    app.set_dotenv("GOOGLE_REFRESH_TOKEN", creds.refresh_token)

    # Create the Drive folders the pipeline expects, so the Shortcut has somewhere to save.
    cfg = config.load()
    drv = google.Drive(creds)
    d = cfg.drive
    for path in (d.inbox_folder, f"{d.inbox_folder}/{d.done_folder}", f"{d.inbox_folder}/{d.backfill_folder}", d.state_folder):
        drv.folder_id(path, create=True)
    print("Drive folders ready.\n\nGOOGLE_REFRESH_TOKEN written to .env. Add the same value as a GitHub Secret:\n\n"
          + creds.refresh_token)


def tiktok_auth() -> None:
    key, secret = app.env("TIKTOK_CLIENT_KEY"), app.env("TIKTOK_CLIENT_SECRET")
    redirect = os.environ.get("TIKTOK_REDIRECT_URI", DEFAULT_REDIRECT)
    state = secrets.token_urlsafe(16)
    code = _paste_code(tiktok.authorize_url(key, redirect, state), state)
    tokens = tiktok.exchange_code(key, secret, code, redirect)
    _save_tokens("tiktok", tokens)


def instagram_auth() -> None:
    app_id, secret = app.env("INSTAGRAM_APP_ID"), app.env("INSTAGRAM_APP_SECRET")
    redirect = os.environ.get("INSTAGRAM_REDIRECT_URI", DEFAULT_REDIRECT)
    state = secrets.token_urlsafe(16)
    code = _paste_code(instagram.authorize_url(app_id, redirect, state), state)
    tokens = instagram.exchange_code(app_id, secret, redirect, code)
    _save_tokens("instagram", tokens)


COMMANDS = {"keygen": keygen, "google": google_auth, "tiktok": tiktok_auth, "instagram": instagram_auth}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crosspost.auth", description="One-off OAuth setup per platform.")
    parser.add_argument("platform", choices=list(COMMANDS))
    args = parser.parse_args(argv)
    app.load_dotenv()
    COMMANDS[args.platform]()
    return 0


if __name__ == "__main__":
    sys.exit(main())

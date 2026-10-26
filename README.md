# crosspost

Personal pipeline: when I post a video on TikTok, repost it to **Instagram Reels** and
**YouTube Shorts** with the caption adapted per platform. Also backfills older TikToks
(≤180 s) that never made it to Instagram/YouTube.

Python 3.12, a handful of dependencies, runs free on GitHub Actions every 15 minutes.

## How it works

```
 iPhone                         Google Drive                       GitHub Actions (every 15 min)
 ──────                         ────────────                       ─────────────────────────────
 post on TikTok                 /tiktok-inbox/            ◀──┐     1. TikTok video.list → new post IDs
   └─ TikTok saves the video      tt-20261026T161500+0100.mov │     2. match post ↔ inbox file
      to the camera roll          /done/        ◀─────────────┼──      (time window AND duration ±1 s)
 close TikTok                     /backfill/    ◀── backfill  │     3. caption per platform
   └─ Shortcut automation       /crosspost-state/             │     4. Instagram (rupload) ─┐ independent,
      copies recent videos ───▶   state.json.enc  ◀──────────┘     5. YouTube (resumable)  ─┘ retried
      to /tiktok-inbox                (encrypted state + tokens)    6. record status, move file to /done
```

- **TikTok** gives metadata only (Display API v2: `id, create_time, video_description, title,
  duration, share_url`). The video file comes from Drive, put there by the iOS Shortcut
  ([SHORTCUT.md](SHORTCUT.md)).
- **Matching**: a post is paired with an inbox file only if the file's timestamp is within
  `matching.window_minutes` of `create_time` **and** its duration (ffprobe) is within
  `matching.duration_tolerance_s`. The file's timestamp is, in order of preference: the
  stamp in the Shortcut's filename, the video's own creation metadata, then Drive's
  upload time. Inbox files that match nothing are ignored, never posted. A post with no
  file is retried for `matching.unmatched_retry_hours` (24 h), then flagged and the run
  fails once so you hear about it.
- **Captions**: TikTok-only tags (`#fyp #foryou #foryoupage #fy #viral`, configurable) are
  stripped. Instagram: ≤2200 chars, ≤30 hashtags. YouTube: title = first line/sentence,
  ≤100 chars, plus ` #Shorts` if it fits. The description is the full cleaned caption.
- **Publishing**: Instagram and YouTube are independent. If one fails, the other still
  goes ahead, and the failure is retried on later runs up to `retries.max_attempts`. Limits
  aren't failures: if the Instagram 24 h publishing limit (checked before every publish) or
  the YouTube daily cap is reached, the post simply waits for a later run.
- **State** is one Fernet-encrypted JSON file in Drive: processed TikTok IDs, per-platform
  status/attempts/post IDs, the backfill queue, daily counters, and the rotating
  TikTok/Instagram tokens. The key exists only in GitHub Secrets and your local `.env`.
  Rotated tokens are saved the moment they're issued (including during dry runs).
- **First run**: only TikToks from about an hour before the first run onwards are picked
  up. Anything older is handled by the backfill.

### Code map

| | |
|---|---|
| `crosspost/run.py` | scheduled job entry point (`python -m crosspost.run [--dry-run]`) |
| `crosspost/job.py` | the pipeline steps, written against injected clients so it's testable |
| `crosspost/captions.py`, `matching.py` | pure logic |
| `crosspost/state.py`, `store.py` | state model; encrypted load/save in Drive |
| `crosspost/clients/` | TikTok, Instagram, Google (Drive + YouTube) |
| `crosspost/auth.py` | one-off OAuth per platform |
| `crosspost/backfill.py` | local backfill: scan, download, approve, status |
| `config.yaml` | every tunable: folders, tag strip list, privacy, caps, match window |
| `docs/callback.html` | OAuth landing page to host at `georgehastings.me/crosspost/callback/` |

### Exit codes and alerts

A failed Actions run is the alert. The job exits **1** when a platform gives up after max
attempts, when a post goes unmatched past the retry window, or when anything else breaks
(auth, Drive, crash). "Nothing new", a retryable failure, or a post waiting on a limit all
exit **0**. Logs are one `logfmt` line per event, e.g.

```
2026-10-26T16:30:04Z info published post_id=17890 tiktok_id=7430 platform=instagram source=live
```

On Actions, warnings and errors also appear as annotations on the run page. Turn on
email notifications for failed workflow runs: GitHub → Settings → Notifications →
Actions.

## Setup

You'll need Python 3.12 and `ffmpeg` locally (`brew install ffmpeg python@3.12`).

```sh
git clone https://github.com/<you>/crosspost && cd crosspost
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-local.txt
cp .env.example .env    # fill in as you go; .env is gitignored
```

### 0. Host the policy pages and the OAuth callback

The TikTok and Meta app forms need public URLs for a privacy policy and terms of service,
and both use an https redirect URI. Put these on georgehastings.me:

| File | URL |
|---|---|
| `PRIVACY.md` (rendered) | `https://georgehastings.me/crosspost/privacy/` |
| `TERMS.md` (rendered) | `https://georgehastings.me/crosspost/terms/` |
| `docs/callback.html` | `https://georgehastings.me/crosspost/callback/` |

The callback page only shows the URL it was opened with, so you can copy the
authorisation code back into the terminal. Nothing is sent anywhere.

### 1. Google Cloud: YouTube Data API + Drive API

1. Go to <https://console.cloud.google.com>, create a project (e.g. `crosspost`).
2. **APIs & Services → Library**: enable **YouTube Data API v3** and **Google Drive API**.
3. **Google Auth Platform** (formerly "OAuth consent screen"):
   - Branding: app name, your email as the support and developer contact.
   - Audience: **External**. Then click **Publish app** so the status reads
     **In production**.
     > ⚠️ **This step matters.** While the app is in *Testing*, Google expires refresh
     > tokens after **7 days** and the pipeline would break every week. *In production*
     > with an unverified app is fine for personal use. You'll see a "Google hasn't
     > verified this app" warning when signing in: click **Advanced → Go to crosspost**.
   - Data access: add the scopes `.../auth/youtube.upload` and `.../auth/drive`. Full Drive
     access is needed because the narrower `drive.file` scope can't see files the iOS
     Shortcut uploads.
4. **Clients → Create client → Desktop app**. Put the ID and secret in `.env` as
   `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
5. Make sure the Google account has a YouTube channel.
6. Run:
   ```sh
   python -m crosspost.auth keygen   # writes CROSSPOST_STATE_KEY to .env
   python -m crosspost.auth google   # browser sign-in; writes GOOGLE_REFRESH_TOKEN; creates the Drive folders
   ```

**YouTube privacy**: uploads from API projects that haven't passed Google's *YouTube API
Services audit* are locked to **private**, whatever you request. So `youtube.privacy_status`
defaults to `private`. If you later pass the audit (via the "YouTube API Services - Audit
and Quota Extension Form"), set it to `public`. Each upload costs ~1600 of the default
10,000 daily quota units, so about 6 a day; `youtube.daily_upload_limit` enforces this,
with the day boundary at midnight Pacific.

### 2. TikTok: Login Kit + Display API

1. At <https://developers.tiktok.com>, log in and go to **Manage apps → Connect an app**.
2. App details: name, icon, category, description. Set **Terms of Service URL** and
   **Privacy Policy URL** to the pages from step 0. Platform: **Web**, with
   `https://georgehastings.me` as the website. If TikTok asks you to verify the domain,
   add the DNS TXT record or signature file it gives you.
3. **Add products**: **Login Kit** and **Display API**.
4. Scopes: **user.info.basic** and **video.list**.
5. Login Kit **Redirect URI**: `https://georgehastings.me/crosspost/callback/`.
6. **Sandbox or production**:
   - **Sandbox** works straight away without review. Create a sandbox, add your own TikTok
     account under **Target users**, and use the sandbox's client key and secret. This is
     enough for a personal tool.
   - **Production** needs app review: a description of the use case and a demo video of the
     sign-in and data use, with the privacy and terms URLs live. Submit it if sandbox
     limits get in the way. Production has a *different* client key and secret, and you
     need to run `auth tiktok` again after switching.
7. Put the key and secret in `.env` as `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET`, then run:
   ```sh
   python -m crosspost.auth tiktok
   ```
   Open the printed URL, approve, copy the URL you land on, and paste it in. Tokens go
   into the encrypted state in Drive. Access tokens last about 24 h and refresh tokens
   about a year; the job refreshes and saves them automatically. Each refresh rotates the
   refresh token.

Also, in the TikTok app turn on saving your posts to the device (Settings and privacy →
Accessibility / "Save posted videos", depending on app version). Otherwise there's
nothing for the Shortcut to copy.

### 3. Instagram: Meta app with Instagram Login

Your Instagram account must be **Professional** (Creator or Business). It doesn't need a
Facebook Page; this uses *Instagram Login*, not Facebook Login.

1. At <https://developers.facebook.com/apps>, go to **Create app**. Pick the use case
   **Manage messaging & content on Instagram** and choose **Business** as the app type if
   asked.
2. In the app, open **Instagram → API setup with Instagram login**.
   - Note the **Instagram app ID** and **Instagram app secret**. These are *not* the
     Facebook app ID/secret. Put them in `.env` as `INSTAGRAM_APP_ID` and
     `INSTAGRAM_APP_SECRET`.
   - Under **Set up Instagram business login → Business login settings**, add the OAuth
     redirect URI `https://georgehastings.me/crosspost/callback/`.
   - Permissions: **instagram_business_basic** and **instagram_business_content_publish**.
3. **App roles → Roles → Instagram testers**: add your Instagram username. In Instagram,
   accept the invite (Settings → Website permissions → Apps and websites → Tester
   invites, or on the web at instagram.com).
4. **App Review is optional.** An app in development mode can act for accounts that have a
   role on it, which is you. To switch the app to Live, Meta requires the privacy policy
   URL (and for advanced access, App Review).
5. Run:
   ```sh
   python -m crosspost.auth instagram
   ```
   This stores a 60-day long-lived token in the encrypted state. The job refreshes it
   automatically once it's within 15 days of expiry.

The pipeline publishes with `media_type=REELS` and `upload_type=resumable`, sending the
bytes to `rupload.facebook.com`, then polls `status_code` until `FINISHED` before calling
`media_publish`. It doesn't use `video_url`, because Drive links aren't direct file URLs.

### 4. iOS Shortcut

Follow [SHORTCUT.md](SHORTCUT.md).

### 5. GitHub repo and secrets

Push this repo to GitHub as a **public** repo, which gives unlimited free Actions minutes.
Then go to **Settings → Secrets and variables → Actions → New repository secret** and add
each of these:

| Secret | Where it comes from |
|---|---|
| `CROSSPOST_STATE_KEY` | `auth keygen` (in `.env`) |
| `GOOGLE_CLIENT_ID` | Google Cloud OAuth client |
| `GOOGLE_CLIENT_SECRET` | Google Cloud OAuth client |
| `GOOGLE_REFRESH_TOKEN` | `auth google` (in `.env`) |
| `TIKTOK_CLIENT_KEY` | TikTok app (sandbox or production) |
| `TIKTOK_CLIENT_SECRET` | TikTok app |

The Instagram app secret isn't needed in Actions; it's only used locally by `auth`.

Secrets are never visible in a public repo, and neither is the state, which lives
encrypted in your Drive. GitHub disables scheduled workflows in public repos after 60
days without activity; `.github/workflows/keepalive.yml` re-enables them monthly to
prevent that.

### 6. Try it

```sh
python -m crosspost.run --dry-run   # locally: shows matches and the captions it would post
```

Or in GitHub, go to **Actions → crosspost → Run workflow** and tick **Dry run**. Once it
looks right, the schedule takes over. Dry run does everything except publish, move files,
or save progress. Rotated tokens are still saved, since TikTok has already invalidated
the old ones.

## Backfill (run locally)

TikTok often blocks datacenter IPs, so this runs on your machine, not in Actions.

```sh
python -m crosspost.backfill --dry-run
```
This lists every TikTok, keeps those ≤180 s that aren't already in state, and fetches your
Instagram posts to fuzzy-match captions (hashtags and punctuation are ignored). The output
looks like this:
```
SKIP (on IG, 0.97)  7301… 2025-03-02    24s  'Sunset run!'
      ↳ IG https://www.instagram.com/reel/…  'Sunset run! #running'
CHECK (maybe on IG, 0.74)  …   ← review these yourself
NEW  …
```
Adjust `backfill.caption_match_threshold` if the matches look wrong.

```sh
python -m crosspost.backfill
```
This does the same, then downloads every candidate with yt-dlp into `backfill/`, choosing a
watermark-free format when one exists (it warns when there isn't). **Watch them, and
delete any file you don't want posted.**

```sh
python -m crosspost.backfill approve --all     # or: approve <tiktok_id> ...
```
This uploads the remaining files to `/tiktok-inbox/backfill` and adds them to the queue.
The scheduled job drains the queue oldest first, at most `backfill.max_per_day` per
platform. Live posts always go first, within the Instagram publishing limit and the
YouTube daily cap.

```sh
python -m crosspost.backfill status            # what's queued, pending, failed; today's counters
```

Don't run `approve` while an Actions run is in progress. The state store refuses to
overwrite a file that changed since it was read, so one side will fail rather than lose
data; just run it again.

## Development

```sh
python -m pytest -q
```
Tests cover caption transforms, matching, state/encryption/counters, the job
orchestration (fakes for every client), and the TikTok/Instagram clients against a
scripted fake HTTP session. None of them make network calls.

## Troubleshooting

- **`TikTok refresh token expired` / `Instagram token expired`**: run `auth tiktok` or
  `auth instagram` again.
- **Google `invalid_grant` after a week**: the consent screen is still in *Testing*.
  Publish it (step 1.3), then run `auth google` again and update `GOOGLE_REFRESH_TOKEN`.
- **`state file changed in Drive since it was loaded`**: two writers overlapped (usually
  a local backfill and an Actions run). Re-run. If a TikTok token rotated in the run that
  failed to save, the next run will report an invalid refresh token: run `auth tiktok`.
- **A post was flagged as unmatched**: check that the Shortcut ran and the file is in
  `/tiktok-inbox`. Widen `matching.window_minutes` if the save time drifts. Flagged posts
  aren't retried; post that one by hand.
- **Leftover files in `/tiktok-inbox`** are ones that matched no post (for example
  duplicates from the Shortcut). They're ignored and safe to delete.
- **Known edge case**: if a run dies between a successful publish and saving state, the
  next run will post that video again. State is saved immediately after every publish to
  keep that window tiny.

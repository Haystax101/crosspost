"""Google OAuth (one refresh token covers YouTube upload + Drive), YouTube and Drive clients."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload, MediaIoBaseUpload

from . import ApiError, Deferred

TOKEN_URI = "https://oauth2.googleapis.com/token"
# Full Drive scope: the narrower drive.file scope can't see files the iOS Shortcut uploads.
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"
CHUNK = 8 * 1024 * 1024


def credentials(client_id: str, client_secret: str, refresh_token: str) -> Credentials:
    return Credentials(
        None, refresh_token=refresh_token, token_uri=TOKEN_URI, client_id=client_id, client_secret=client_secret, scopes=SCOPES
    )


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    created_time: int
    duration: float | None  # from Drive's own video metadata, when it has processed the file


class Drive:
    def __init__(self, creds: Credentials):
        self.svc = build("drive", "v3", credentials=creds, cache_discovery=False)

    def folder_id(self, path: str, create: bool = False) -> str:
        """Resolve a slash-separated folder path from My Drive root, e.g. 'tiktok-inbox/done'."""
        parent = "root"
        for name in [p for p in path.split("/") if p]:
            res = self.svc.files().list(
                q=f"name = '{_q(name)}' and mimeType = '{FOLDER_MIME}' and '{parent}' in parents and trashed = false",
                fields="files(id)",
                pageSize=2,
            ).execute()
            files = res.get("files", [])
            if files:
                parent = files[0]["id"]
            elif create:
                parent = self.svc.files().create(
                    body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]}, fields="id"
                ).execute()["id"]
            else:
                raise ApiError(f"Drive folder not found: {path}")
        return parent

    def list_videos(self, folder_id: str) -> list[DriveFile]:
        from .. import media

        out, token = [], None
        while True:
            res = self.svc.files().list(
                q=f"'{folder_id}' in parents and trashed = false and mimeType contains 'video/'",
                fields="nextPageToken, files(id, name, createdTime, videoMediaMetadata(durationMillis))",
                pageSize=100,
                pageToken=token,
            ).execute()
            for f in res.get("files", []):
                millis = f.get("videoMediaMetadata", {}).get("durationMillis")
                out.append(
                    DriveFile(
                        id=f["id"],
                        name=f["name"],
                        created_time=media.parse_time(f["createdTime"]) or 0,
                        duration=int(millis) / 1000 if millis else None,
                    )
                )
            token = res.get("nextPageToken")
            if not token:
                return out

    def download(self, file_id: str, dest: Path) -> Path:
        with open(dest, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, self.svc.files().get_media(fileId=file_id), chunksize=CHUNK)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return dest

    def move(self, file_id: str, folder_id: str) -> None:
        parents = self.svc.files().get(fileId=file_id, fields="parents").execute().get("parents", [])
        self.svc.files().update(
            fileId=file_id, addParents=folder_id, removeParents=",".join(parents), fields="id"
        ).execute()

    def upload(self, folder_id: str, path: Path, name: str) -> str:
        media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=CHUNK, resumable=True)
        return self.svc.files().create(body={"name": name, "parents": [folder_id]}, media_body=media, fields="id").execute()["id"]

    # --- small binary files (the encrypted state) --------------------------

    def find_file(self, folder_id: str, name: str) -> tuple[str, str] | None:
        res = self.svc.files().list(
            q=f"name = '{_q(name)}' and '{folder_id}' in parents and trashed = false",
            fields="files(id, modifiedTime)",
            pageSize=2,
        ).execute()
        files = res.get("files", [])
        return (files[0]["id"], files[0]["modifiedTime"]) if files else None

    def modified_time(self, file_id: str) -> str:
        return self.svc.files().get(fileId=file_id, fields="modifiedTime").execute()["modifiedTime"]

    def read_bytes(self, file_id: str) -> bytes:
        return self.svc.files().get_media(fileId=file_id).execute()

    def write_bytes(self, folder_id: str, name: str, data: bytes, file_id: str | None = None) -> tuple[str, str]:
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype="application/octet-stream")
        if file_id:
            res = self.svc.files().update(fileId=file_id, media_body=media, fields="id, modifiedTime").execute()
        else:
            res = self.svc.files().create(
                body={"name": name, "parents": [folder_id]}, media_body=media, fields="id, modifiedTime"
            ).execute()
        return res["id"], res["modifiedTime"]


QUOTA_REASONS = {"quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded", "rateLimitExceeded"}


class YouTube:
    def __init__(self, creds: Credentials):
        self.svc = build("youtube", "v3", credentials=creds, cache_discovery=False)

    def upload(self, path: Path, title: str, description: str, privacy_status: str, category_id: str, made_for_kids: bool) -> str:
        body = {
            "snippet": {"title": title, "description": description, "categoryId": category_id},
            "status": {"privacyStatus": privacy_status, "selfDeclaredMadeForKids": made_for_kids},
        }
        request = self.svc.videos().insert(
            part="snippet,status",
            body=body,
            media_body=MediaFileUpload(str(path), mimetype="video/*", chunksize=CHUNK, resumable=True),
        )
        try:
            response = None
            while response is None:
                _, response = request.next_chunk()
        except HttpError as e:
            reasons = {d.get("reason") for d in (e.error_details or []) if isinstance(d, dict)}
            if reasons & QUOTA_REASONS:
                raise Deferred(f"YouTube quota/limit: {', '.join(sorted(reasons))}") from e
            raise ApiError(f"YouTube upload failed: {e}") from e
        return response["id"]

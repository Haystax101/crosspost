"""Encrypted state persistence in Google Drive.

The state (including rotating TikTok/Instagram tokens) is one Fernet-encrypted
file in Drive. The key lives only in the CROSSPOST_STATE_KEY secret / local .env.

Saves are guarded by a simple optimistic check: if the file changed in Drive since
we loaded it (e.g. a local backfill ran mid-job), we refuse to overwrite it.
"""

from __future__ import annotations

from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from .state import State


class StateConflict(RuntimeError):
    pass


def generate_key() -> str:
    return Fernet.generate_key().decode()


def encrypt(state: State, key: str) -> bytes:
    return Fernet(key.encode()).encrypt(state.to_json())


def decrypt(blob: bytes, key: str) -> State:
    try:
        return State.from_json(Fernet(key.encode()).decrypt(blob))
    except InvalidToken:
        raise RuntimeError("could not decrypt state: wrong CROSSPOST_STATE_KEY?") from None


class Store(Protocol):
    def load(self) -> State: ...
    def save(self, state: State) -> None: ...


class DriveStore:
    def __init__(self, drive, folder_name: str, file_name: str, key: str):
        self.drive = drive
        self.folder_name = folder_name
        self.file_name = file_name
        self.key = key
        self._file_id: str | None = None
        self._modified: str | None = None
        self._folder_id: str | None = None

    def load(self) -> State:
        self._folder_id = self.drive.folder_id(self.folder_name, create=True)
        found = self.drive.find_file(self._folder_id, self.file_name)
        if found is None:
            return State()
        self._file_id, self._modified = found
        return decrypt(self.drive.read_bytes(self._file_id), self.key)

    def save(self, state: State) -> None:
        if self._folder_id is None:
            raise RuntimeError("save() before load()")
        if self._file_id is not None and self.drive.modified_time(self._file_id) != self._modified:
            raise StateConflict("state file changed in Drive since it was loaded; not overwriting")
        self._file_id, self._modified = self.drive.write_bytes(
            self._folder_id, self.file_name, encrypt(state, self.key), file_id=self._file_id
        )

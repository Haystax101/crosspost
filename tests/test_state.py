import pytest
from cryptography.fernet import Fernet

from crosspost.state import DONE, PENDING, State, Video
from crosspost.store import DriveStore, StateConflict, decrypt, encrypt, generate_key


def sample() -> State:
    s = State(watermark=123)
    v = s.add_video(Video(id="1", create_time=100, caption="hi", duration=12.0), ["instagram", "youtube"])
    v.platforms["instagram"].status = DONE
    v.platforms["instagram"].post_id = "ig1"
    s.tokens["tiktok"] = {"access_token": "a", "refresh_token": "r"}
    return s


def test_roundtrip_json():
    s = sample()
    back = State.from_json(s.to_json())
    assert back == s
    assert back.videos["1"].pending_platforms() == ["youtube"]
    assert back.videos["1"].platforms["youtube"].status == PENDING


def test_settled():
    v = sample().videos["1"]
    assert not v.settled()
    v.platforms["youtube"].status = DONE
    assert v.settled()


def test_encrypt_roundtrip_and_wrong_key():
    key = generate_key()
    blob = encrypt(sample(), key)
    assert b"refresh_token" not in blob
    assert decrypt(blob, key) == sample()
    with pytest.raises(RuntimeError, match="wrong CROSSPOST_STATE_KEY"):
        decrypt(blob, Fernet.generate_key().decode())


def test_counters_reset_at_pacific_midnight():
    s = State()
    t = 1_800_000_000  # 2027-01-15 08:00 UTC = 00:00 PST
    s.bump("youtube_uploads", t - 1)  # 23:59:59 Pacific previous day
    assert s.count("youtube_uploads", t - 1) == 1
    assert s.count("youtube_uploads", t) == 0
    s.bump("youtube_uploads", t)
    s.bump("youtube_uploads", t + 3600)
    assert s.count("youtube_uploads", t + 3600) == 2


def test_rejects_unknown_version():
    with pytest.raises(ValueError):
        State.from_json(b'{"version": 99}')


class MemDrive:
    """Just enough of the Drive client for DriveStore."""

    def __init__(self):
        self.blob = None
        self.mtime = 0

    def folder_id(self, name, create=False):
        return "folder"

    def find_file(self, folder_id, name):
        return ("state", str(self.mtime)) if self.blob is not None else None

    def read_bytes(self, file_id):
        return self.blob

    def modified_time(self, file_id):
        return str(self.mtime)

    def write_bytes(self, folder_id, name, data, file_id=None):
        self.blob, self.mtime = data, self.mtime + 1
        return "state", str(self.mtime)


def test_drive_store_load_save_and_conflict():
    key = generate_key()
    drive = MemDrive()
    a = DriveStore(drive, "f", "s", key)
    assert a.load() == State()  # nothing stored yet
    a.save(sample())
    a.save(sample())  # consecutive saves by the same writer are fine

    b = DriveStore(drive, "f", "s", key)
    assert b.load() == sample()
    a.save(State(watermark=1))  # someone else writes in between...
    with pytest.raises(StateConflict):
        b.save(sample())  # ...so b must not clobber it

"""Minimal user-session single-instance protection for the GUI runtime."""

from __future__ import annotations

import fcntl
import os
import tempfile
from pathlib import Path
from types import TracebackType

from screen_audio_router.identity import APPLICATION_ID


def instance_lock_path(environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    runtime_dir = env.get("XDG_RUNTIME_DIR")
    base = Path(runtime_dir) if runtime_dir else Path(tempfile.gettempdir()) / f"user-{os.getuid()}"
    return base / f"{APPLICATION_ID}.lock"


class InstanceGuard:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or instance_lock_path()
        self._file = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_file = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            return False
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"{os.getpid()}\n")
        lock_file.flush()
        self._file = lock_file
        return True

    def release(self) -> None:
        if self._file is None:
            return
        fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("Screen Audio Router is already running in this user session")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.release()

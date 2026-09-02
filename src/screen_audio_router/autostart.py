"""Safe per-user XDG autostart management."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

AUTOSTART_FILENAME = "screen-audio-router.desktop"
MANAGED_MARKER = "X-ScreenAudioRouter-Managed=true"


class AutostartState(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AutostartStatus:
    state: AutostartState
    path: Path
    message: str

    @property
    def enabled(self) -> bool:
        return self.state is AutostartState.ENABLED


class AutostartError(OSError):
    pass


def autostart_path(environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    base = Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / AUTOSTART_FILENAME


class AutostartManager:
    def __init__(
        self,
        path: Path | None = None,
        launch_command: tuple[str, ...] = ("screen-audio-router", "--autostart"),
        *,
        executable_available=None,
    ) -> None:
        self.path = path or autostart_path()
        self.launch_command = launch_command
        self._executable_available = executable_available or shutil.which

    def status(self) -> AutostartStatus:
        if not self.path.exists():
            return AutostartStatus(AutostartState.DISABLED, self.path, "Autostart is disabled")
        try:
            content = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            return AutostartStatus(AutostartState.ERROR, self.path, str(exc))
        if not self._is_managed(content):
            return AutostartStatus(
                AutostartState.ERROR,
                self.path,
                "The autostart target exists but is not managed by Screen Audio Router",
            )
        command = " ".join(_escape_exec_argument(item) for item in self.launch_command)
        required = (
            "[Desktop Entry]",
            "Type=Application",
            "Name=Screen Audio Router",
            f"Exec={command}",
            "Terminal=false",
        )
        if not all(item in content for item in required):
            return AutostartStatus(
                AutostartState.ERROR, self.path, "The managed autostart entry is incomplete"
            )
        return AutostartStatus(AutostartState.ENABLED, self.path, "Autostart is enabled")

    def enable(self) -> AutostartStatus:
        existing = self.status()
        if existing.state is AutostartState.ERROR:
            raise AutostartError(existing.message)
        executable = self.launch_command[0]
        if os.sep not in executable and self._executable_available(executable) is None:
            raise AutostartError(
                f"Launch command {executable!r} is not available on PATH; install the package first"
            )
        if os.sep in executable and not os.access(executable, os.X_OK):
            raise AutostartError(f"Launch executable is unavailable: {executable}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = self.desktop_entry()
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        verified = self.status()
        if not verified.enabled:
            raise AutostartError(f"Could not verify autostart entry: {verified.message}")
        return verified

    def disable(self) -> AutostartStatus:
        current = self.status()
        if current.state is AutostartState.ERROR:
            raise AutostartError(current.message)
        if current.enabled:
            self.path.unlink()
        return self.status()

    def desktop_entry(self) -> str:
        command = " ".join(_escape_exec_argument(item) for item in self.launch_command)
        return (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Screen Audio Router\n"
            f"Exec={command}\n"
            "Icon=audio-card\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
            f"{MANAGED_MARKER}\n"
        )

    @staticmethod
    def _is_managed(content: str) -> bool:
        return MANAGED_MARKER in content.splitlines()


def _escape_exec_argument(argument: str) -> str:
    if not argument or "\n" in argument or "\r" in argument:
        raise AutostartError("Autostart command contains an invalid argument")
    if re.fullmatch(r"[A-Za-z0-9_./:+-]+", argument):
        return argument
    escaped = argument.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'

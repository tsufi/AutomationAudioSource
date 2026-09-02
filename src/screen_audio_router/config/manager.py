"""Safe loading and atomic persistence of application configuration."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from screen_audio_router.config.schema import AppConfig, ConfigError


def default_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "screen-audio-router" / "config.json"


class ConfigurationStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_config_path()

    def load(self) -> AppConfig:
        if not self.path.exists():
            config = AppConfig()
            self.save(config)
            return config
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"could not read {self.path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError("configuration root must be an object")
        return AppConfig.from_dict(data)

    @staticmethod
    def default() -> AppConfig:
        return AppConfig()

    @staticmethod
    def validate(config: AppConfig) -> None:
        config.validate()

    def save(self, config: AppConfig) -> None:
        config.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(config.to_dict(), indent=2, sort_keys=True) + "\n"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as temporary:
                temporary.write(payload)
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


ConfigManager = ConfigurationStore

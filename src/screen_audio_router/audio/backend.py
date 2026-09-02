"""Replaceable audio backend contract and typed failures."""

from __future__ import annotations

from typing import Protocol

from screen_audio_router.audio.models import AudioCard, AudioOutput, AudioStream, ServerInfo


class AudioBackendError(RuntimeError):
    """Base class for expected audio backend failures."""


class PactlNotFoundError(AudioBackendError):
    pass


class AudioServerUnavailableError(AudioBackendError):
    pass


class AudioDataError(AudioBackendError):
    pass


class AudioObjectNotFoundError(AudioBackendError):
    pass


class AudioCommandError(AudioBackendError):
    pass


class AudioBackend(Protocol):
    def server_info(self) -> ServerInfo: ...

    def list_outputs(self) -> list[AudioOutput]: ...

    def list_streams(self) -> list[AudioStream]: ...

    def list_cards(self) -> list[AudioCard]: ...

    def get_stream_output(self, stream_id: str) -> str | None: ...

    def move_stream(self, stream_id: str, output_id: str) -> None: ...

    def set_card_profile(self, card_id: str, profile: str) -> None: ...

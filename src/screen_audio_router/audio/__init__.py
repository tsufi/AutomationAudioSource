"""Replaceable audio discovery and stream movement API."""

from screen_audio_router.audio.backend import (
    AudioBackend,
    AudioBackendError,
    AudioCommandError,
    AudioDataError,
    AudioObjectNotFoundError,
    AudioServerUnavailableError,
    PactlNotFoundError,
)
from screen_audio_router.audio.models import AudioCard, AudioOutput, AudioStream, ServerInfo
from screen_audio_router.audio.pactl_backend import PactlAudioBackend

__all__ = [
    "AudioBackend",
    "AudioBackendError",
    "AudioCard",
    "AudioCommandError",
    "AudioDataError",
    "AudioObjectNotFoundError",
    "AudioOutput",
    "AudioServerUnavailableError",
    "AudioStream",
    "PactlAudioBackend",
    "PactlNotFoundError",
    "ServerInfo",
]

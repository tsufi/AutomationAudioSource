"""Typed models exposed by audio backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Availability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ServerInfo:
    server_name: str
    server_version: str | None = None
    server_string: str | None = None
    default_output: str | None = None
    is_local: bool | None = None
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AudioOutput:
    """An output identified persistently by its sink name, never its index."""

    sink_name: str
    description: str
    availability: Availability
    state: str | None = None
    card_name: str | None = None
    active_port: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.sink_name


@dataclass(frozen=True, slots=True)
class AudioStream:
    """A live stream whose stream_id is explicitly dynamic and non-persistent."""

    stream_id: str
    application_name: str | None = None
    process_binary: str | None = None
    process_id: int | None = None
    media_name: str | None = None
    current_output: str | None = None
    client_id: int | None = None
    corked: bool | None = None
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AudioProfile:
    name: str
    description: str
    available: bool | None
    sinks: int = 0
    sources: int = 0
    priority: int = 0


@dataclass(frozen=True, slots=True)
class AudioCard:
    card_name: str
    description: str
    active_profile: str | None
    profiles: tuple[AudioProfile, ...]
    properties: dict[str, Any] = field(default_factory=dict)

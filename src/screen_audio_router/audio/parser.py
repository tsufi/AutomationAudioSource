"""Parsing for pactl's machine-readable JSON output."""

from __future__ import annotations

import json
from typing import Any

from screen_audio_router.audio.backend import AudioDataError
from screen_audio_router.audio.models import (
    AudioCard,
    AudioOutput,
    AudioProfile,
    AudioStream,
    Availability,
    ServerInfo,
)


def parse_json_object(raw: str, subject: str) -> dict[str, Any]:
    value = _decode(raw, subject)
    if not isinstance(value, dict):
        raise AudioDataError(f"expected {subject} to be a JSON object")
    return value


def parse_json_list(raw: str, subject: str) -> list[dict[str, Any]]:
    value = _decode(raw, subject)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise AudioDataError(f"expected {subject} to be a JSON array of objects")
    return value


def _decode(raw: str, subject: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AudioDataError(f"malformed pactl JSON for {subject}: {exc.msg}") from exc


def parse_server_info(raw: str) -> ServerInfo:
    item = parse_json_object(raw, "server info")
    server_name = item.get("server_name")
    if not isinstance(server_name, str) or not server_name:
        raise AudioDataError("server info is missing server_name")
    return ServerInfo(
        server_name=server_name,
        server_version=_string(item.get("server_version")),
        server_string=_string(item.get("server_string")),
        default_output=_string(item.get("default_sink_name")),
        is_local=_boolean(item.get("is_local")),
        properties=item,
    )


def parse_outputs(raw: str) -> tuple[list[AudioOutput], dict[int, str]]:
    outputs_by_name: dict[str, AudioOutput] = {}
    index_to_name: dict[int, str] = {}
    for item in parse_json_list(raw, "sinks"):
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        properties = _properties(item)
        index = _integer(item.get("index"))
        if index is not None:
            index_to_name[index] = name
        ports = item.get("ports") if isinstance(item.get("ports"), list) else []
        active_port = _string(item.get("active_port"))
        output = AudioOutput(
            sink_name=name,
            description=_string(item.get("description")) or name,
            availability=_output_availability(ports, active_port),
            state=_string(item.get("state")),
            card_name=_string(properties.get("device.name")),
            active_port=active_port,
            properties=properties,
        )
        previous = outputs_by_name.get(name)
        if previous is None or _output_rank(output) > _output_rank(previous):
            outputs_by_name[name] = output
    return list(outputs_by_name.values()), index_to_name


def parse_streams(raw: str, output_names: dict[int, str]) -> list[AudioStream]:
    streams: list[AudioStream] = []
    for item in parse_json_list(raw, "sink inputs"):
        stream_index = item.get("index")
        if not isinstance(stream_index, (int, str)):
            continue
        properties = _properties(item)
        sink_index = _integer(item.get("sink"))
        streams.append(
            AudioStream(
                stream_id=str(stream_index),
                application_name=_string(properties.get("application.name")),
                process_binary=_string(properties.get("application.process.binary")),
                process_id=_integer(properties.get("application.process.id")),
                media_name=_string(properties.get("media.name")),
                current_output=output_names.get(sink_index) if sink_index is not None else None,
                client_id=_integer(item.get("client")),
                corked=_boolean(item.get("corked")),
                properties=properties,
            )
        )
    return streams


def parse_cards(raw: str) -> list[AudioCard]:
    cards: list[AudioCard] = []
    for item in parse_json_list(raw, "cards"):
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        properties = _properties(item)
        raw_profiles = item.get("profiles")
        profiles: list[AudioProfile] = []
        if isinstance(raw_profiles, dict):
            for profile_name, profile in raw_profiles.items():
                if not isinstance(profile_name, str) or not isinstance(profile, dict):
                    continue
                profiles.append(
                    AudioProfile(
                        name=profile_name,
                        description=_string(profile.get("description")) or profile_name,
                        available=_boolean(profile.get("available")),
                        sinks=_integer(profile.get("sinks")) or 0,
                        sources=_integer(profile.get("sources")) or 0,
                        priority=_integer(profile.get("priority")) or 0,
                    )
                )
        cards.append(
            AudioCard(
                card_name=name,
                description=_string(properties.get("device.description")) or name,
                active_profile=_string(item.get("active_profile")),
                profiles=tuple(profiles),
                properties=properties,
            )
        )
    return cards


def _properties(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("properties")
    return dict(value) if isinstance(value, dict) else {}


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in {"yes", "true"}:
            return True
        if value.lower() in {"no", "false"}:
            return False
    return None


def _output_availability(ports: list[Any], active_port: str | None) -> Availability:
    relevant = [port for port in ports if isinstance(port, dict)]
    if active_port:
        active = [port for port in relevant if port.get("name") == active_port]
        if active:
            relevant = active
    statuses = {str(port.get("availability", "")).lower() for port in relevant}
    if "available" in statuses:
        return Availability.AVAILABLE
    if "not available" in statuses:
        return Availability.UNAVAILABLE
    return Availability.UNKNOWN


def _output_rank(output: AudioOutput) -> tuple[int, int]:
    availability_rank = {
        Availability.UNAVAILABLE: 0,
        Availability.UNKNOWN: 1,
        Availability.AVAILABLE: 2,
    }
    state_rank = {"SUSPENDED": 1, "IDLE": 2, "RUNNING": 3}
    return availability_rank[output.availability], state_rank.get(output.state or "", 0)

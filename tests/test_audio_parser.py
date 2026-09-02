from pathlib import Path

import pytest

from screen_audio_router.audio.backend import AudioDataError
from screen_audio_router.audio.models import Availability
from screen_audio_router.audio.parser import (
    parse_cards,
    parse_outputs,
    parse_server_info,
    parse_streams,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_server_info_parsing():
    info = parse_server_info(fixture("pactl_info.json"))
    assert info.server_name == "PulseAudio (on PipeWire 1.2.3)"
    assert info.default_output == "sink.usb"
    assert info.is_local is True


def test_sink_parsing_uses_stable_names_and_availability():
    outputs, indexes = parse_outputs(fixture("pactl_sinks.json"))
    assert [output.sink_name for output in outputs] == ["sink.usb", "sink.hdmi"]
    assert outputs[0].card_name == "card.usb"
    assert outputs[0].availability is Availability.AVAILABLE
    assert outputs[1].availability is Availability.UNAVAILABLE
    assert indexes == {54: "sink.usb", 73: "sink.hdmi"}


def test_duplicate_dynamic_sink_objects_collapse_to_stable_name():
    raw = """[
      {"index": 1, "name": "sink.same", "state": "SUSPENDED", "ports": []},
      {"index": 2, "name": "sink.same", "state": "RUNNING", "ports": []}
    ]"""
    outputs, indexes = parse_outputs(raw)
    assert len(outputs) == 1
    assert outputs[0].state == "RUNNING"
    assert indexes == {1: "sink.same", 2: "sink.same"}


def test_stream_parsing_resolves_dynamic_sink_index_to_name():
    streams = parse_streams(fixture("pactl_streams.json"), {54: "sink.usb"})
    assert streams[0].stream_id == "327"
    assert streams[0].process_binary == "mpv"
    assert streams[0].process_id == 12345
    assert streams[0].current_output == "sink.usb"
    assert streams[1].application_name is None
    assert streams[1].current_output is None


def test_empty_stream_list():
    assert parse_streams("[]", {}) == []


def test_card_and_profile_parsing():
    card = parse_cards(fixture("pactl_cards.json"))[0]
    assert card.card_name == "card.pci"
    assert card.active_profile == "output:hdmi"
    assert card.profiles[1].available is True
    assert card.profiles[2].available is False


@pytest.mark.parametrize("raw", ["", "not json", "{}", "[1]"])
def test_malformed_or_wrong_shaped_list_output(raw):
    with pytest.raises(AudioDataError):
        parse_outputs(raw)


def test_server_info_missing_required_field():
    with pytest.raises(AudioDataError, match="server_name"):
        parse_server_info("{}")

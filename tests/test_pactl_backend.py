import subprocess
from pathlib import Path

import pytest

from screen_audio_router.audio.backend import (
    AudioCommandError,
    AudioObjectNotFoundError,
    AudioServerUnavailableError,
    PactlNotFoundError,
)
from screen_audio_router.audio.pactl_backend import PactlAudioBackend

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class FakeRunner:
    def __init__(self):
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command):
        command = tuple(command)
        self.commands.append(command)
        if command[-2:] == ("list", "sinks"):
            return fixture("pactl_sinks.json")
        if command[-2:] == ("list", "sink-inputs"):
            return fixture("pactl_streams.json")
        if command[-2:] == ("list", "cards"):
            return fixture("pactl_cards.json")
        if "move-sink-input" in command or "set-card-profile" in command:
            return ""
        raise AssertionError(f"unexpected command: {command}")


def test_get_stream_output():
    assert PactlAudioBackend(FakeRunner()).get_stream_output("327") == "sink.usb"


def test_move_requires_existing_stream_and_output():
    backend = PactlAudioBackend(FakeRunner())
    with pytest.raises(AudioObjectNotFoundError, match="output"):
        backend.move_stream("327", "sink.missing")
    with pytest.raises(AudioObjectNotFoundError, match="stream"):
        backend.move_stream("999", "sink.usb")
    with pytest.raises(AudioObjectNotFoundError, match="invalid"):
        backend.move_stream("not-a-number", "sink.usb")


def test_move_uses_explicit_stream_and_stable_output():
    runner = FakeRunner()
    PactlAudioBackend(runner).move_stream("327", "sink.hdmi")
    assert runner.commands[-1] == ("pactl", "move-sink-input", "327", "sink.hdmi")


def test_profile_validation_and_command():
    runner = FakeRunner()
    backend = PactlAudioBackend(runner)
    backend.set_card_profile("card.pci", "output:hdmi")
    assert runner.commands[-1] == ("pactl", "set-card-profile", "card.pci", "output:hdmi")
    with pytest.raises(AudioObjectNotFoundError, match="unavailable"):
        backend.set_card_profile("card.pci", "output:missing")


def test_pactl_missing_is_typed(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(PactlNotFoundError):
        PactlAudioBackend().list_outputs()


@pytest.mark.parametrize(
    ("stderr", "error"),
    [("Connection refused", AudioServerUnavailableError), ("No such entity", AudioCommandError)],
)
def test_subprocess_failure_is_typed(monkeypatch, stderr, error):
    def failed(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, stdout="", stderr=stderr)

    monkeypatch.setattr(subprocess, "run", failed)
    with pytest.raises(error):
        PactlAudioBackend().list_outputs()

"""PulseAudio-compatible backend implemented exclusively through pactl."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable, Sequence

from screen_audio_router.audio.backend import (
    AudioCommandError,
    AudioObjectNotFoundError,
    AudioServerUnavailableError,
    PactlNotFoundError,
)
from screen_audio_router.audio.models import AudioCard, AudioOutput, AudioStream, ServerInfo
from screen_audio_router.audio.parser import (
    parse_cards,
    parse_outputs,
    parse_server_info,
    parse_streams,
)

CommandRunner = Callable[[Sequence[str]], str]


class PactlAudioBackend:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or self._run_subprocess
        self._logger = logging.getLogger(__name__)

    def server_info(self) -> ServerInfo:
        return parse_server_info(self._json_command("info"))

    def list_outputs(self) -> list[AudioOutput]:
        outputs, _ = parse_outputs(self._json_command("list", "sinks"))
        return outputs

    def list_streams(self) -> list[AudioStream]:
        _, output_names = parse_outputs(self._json_command("list", "sinks"))
        return parse_streams(self._json_command("list", "sink-inputs"), output_names)

    def list_cards(self) -> list[AudioCard]:
        return parse_cards(self._json_command("list", "cards"))

    def get_stream_output(self, stream_id: str) -> str | None:
        self._validate_stream_id(stream_id)
        stream = next((item for item in self.list_streams() if item.stream_id == stream_id), None)
        if stream is None:
            raise AudioObjectNotFoundError(f"audio stream {stream_id!r} does not exist")
        return stream.current_output

    def move_stream(self, stream_id: str, output_id: str) -> None:
        self._validate_stream_id(stream_id)
        if not output_id:
            raise AudioObjectNotFoundError("destination output name cannot be empty")
        if not any(output.sink_name == output_id for output in self.list_outputs()):
            raise AudioObjectNotFoundError(f"audio output {output_id!r} does not exist")
        if not any(stream.stream_id == stream_id for stream in self.list_streams()):
            raise AudioObjectNotFoundError(f"audio stream {stream_id!r} does not exist")
        self._command("move-sink-input", stream_id, output_id)
        self._logger.info("Moved audio stream %s to %s", stream_id, output_id)

    def set_card_profile(self, card_id: str, profile: str) -> None:
        card = next((item for item in self.list_cards() if item.card_name == card_id), None)
        if card is None:
            raise AudioObjectNotFoundError(f"audio card {card_id!r} does not exist")
        selected = next((item for item in card.profiles if item.name == profile), None)
        if selected is None:
            raise AudioObjectNotFoundError(f"profile {profile!r} does not exist on {card_id!r}")
        if selected.available is False:
            raise AudioObjectNotFoundError(f"profile {profile!r} is unavailable on {card_id!r}")
        self._command("set-card-profile", card_id, profile)
        self._logger.info("Set audio card %s profile to %s", card_id, profile)

    def _json_command(self, *arguments: str) -> str:
        return self._runner(("pactl", "--format=json", *arguments))

    def _command(self, *arguments: str) -> str:
        return self._runner(("pactl", *arguments))

    @staticmethod
    def _validate_stream_id(stream_id: str) -> None:
        if not stream_id.isdecimal():
            raise AudioObjectNotFoundError(f"invalid dynamic stream ID: {stream_id!r}")

    @staticmethod
    def _run_subprocess(command: Sequence[str]) -> str:
        try:
            completed = subprocess.run(
                list(command), check=False, capture_output=True, text=True, timeout=10
            )
        except FileNotFoundError as exc:
            raise PactlNotFoundError("pactl was not found; install libpulse/pulseaudio tools") from exc
        except subprocess.TimeoutExpired as exc:
            raise AudioServerUnavailableError("pactl timed out while contacting the audio server") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "unknown pactl error"
            if "connection" in detail.lower() or "daemon" in detail.lower():
                raise AudioServerUnavailableError(detail)
            raise AudioCommandError(f"pactl command failed ({completed.returncode}): {detail}")
        return completed.stdout

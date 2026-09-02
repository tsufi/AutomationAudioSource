"""Qt runtime bridge for live KWin and non-blocking audio discovery."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor

from PySide6.QtCore import QObject, QTimer, Signal

from screen_audio_router.audio import AudioBackendError, AudioOutput, AudioStream, PactlAudioBackend
from screen_audio_router.kwin.cli import reload_kwin_script
from screen_audio_router.kwin.dbus_service import KWinDBusService
from screen_audio_router.kwin.integration import KWinIntegration, WindowEventType
from screen_audio_router.router.application_registry import ApplicationRegistry
from screen_audio_router.router.matcher import ApplicationMatcher


class _WorkerSignals(QObject):
    completed = Signal(object, object)
    failed = Signal(str)


class GuiRuntime(QObject):
    snapshot_changed = Signal(object, object)
    error_occurred = Signal(str)

    def __init__(self, matcher: ApplicationMatcher, poll_interval_ms: int, parent=None) -> None:
        super().__init__(parent)
        self.kwin = KWinIntegration()
        self.registry = ApplicationRegistry(matcher)
        self.backend = PactlAudioBackend()
        self.outputs: list[AudioOutput] = []
        self._service = KWinDBusService(self.kwin)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audio-discovery")
        self._audio_pending = False
        self._started = False
        self._stopped = False
        self._signals = _WorkerSignals()
        self._signals.completed.connect(self._apply_audio_snapshot)
        self._signals.failed.connect(self._audio_failed)
        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval_ms)
        self._timer.timeout.connect(self.refresh_audio)
        self.kwin.add_listener(self._window_event)

    def start(self) -> None:
        self._service.register()
        self._started = True
        try:
            reload_kwin_script()
        except Exception:
            self._service.unregister()
            self._started = False
            raise
        self._timer.start()
        self.refresh_audio()

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._timer.stop()
        if self._started:
            self._service.unregister()
            self._started = False
        self._executor.shutdown(wait=True, cancel_futures=True)

    def refresh_audio(self) -> None:
        if self._audio_pending:
            return
        self._audio_pending = True
        future = self._executor.submit(self._discover_audio)
        future.add_done_callback(self._audio_done)

    def _discover_audio(self) -> tuple[list[AudioStream], list[AudioOutput]]:
        return self.backend.list_streams(), self.backend.list_outputs()

    def _audio_done(self, future: Future[tuple[list[AudioStream], list[AudioOutput]]]) -> None:
        try:
            streams, outputs = future.result()
        except AudioBackendError as exc:
            self._signals.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - worker failures must cross the Qt boundary
            self._signals.failed.emit(f"Unexpected audio discovery error: {exc}")
        else:
            self._signals.completed.emit(streams, outputs)

    def _apply_audio_snapshot(self, streams: list[AudioStream], outputs: list[AudioOutput]) -> None:
        self._audio_pending = False
        streams_changed = self.registry.refresh_streams(streams)
        outputs_changed = outputs != self.outputs
        self.outputs = outputs
        if streams_changed or outputs_changed:
            self._emit_snapshot()

    def _audio_failed(self, message: str) -> None:
        self._audio_pending = False
        self.error_occurred.emit(message)

    def _window_event(self, event) -> None:
        if event.event_type is WindowEventType.SNAPSHOT:
            self.registry.sync_windows(self.kwin.windows(include_all=True))
        else:
            self.registry.handle_window_event(event)
        self._emit_snapshot()

    def _emit_snapshot(self) -> None:
        self.snapshot_changed.emit(self.registry.entries, tuple(self.outputs))

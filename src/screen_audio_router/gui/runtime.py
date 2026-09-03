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
    ready = Signal()
    error_occurred = Signal(str)
    display_refresh_requested = Signal()

    def __init__(self, matcher: ApplicationMatcher, poll_interval_ms: int, parent=None) -> None:
        super().__init__(parent)
        self.kwin = KWinIntegration()
        self.registry = ApplicationRegistry(matcher)
        self.backend = PactlAudioBackend()
        self.outputs: list[AudioOutput] = []
        self._service = KWinDBusService(self.kwin)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audio-discovery")
        self._audio_pending = False
        self._audio_refresh_deferred = False
        self._started = False
        self._stopped = False
        self._window_snapshot_ready = False
        self._audio_snapshot_ready = False
        self._ready_emitted = False
        self.audio_error: str | None = None
        self.kwin_error: str | None = None
        self._signals = _WorkerSignals()
        self._signals.completed.connect(self._apply_audio_snapshot)
        self._signals.failed.connect(self._audio_failed)
        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval_ms)
        self._timer.timeout.connect(self.refresh_audio)
        self.kwin.add_listener(self._window_event)

    def start(self) -> None:
        try:
            self._service.register()
        except RuntimeError as exc:
            self.kwin_error = str(exc)
            raise
        self._started = True
        try:
            reload_kwin_script()
        except Exception:
            self.kwin_error = "KWin script reload failed"
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
        if self._stopped:
            return
        if self._audio_pending:
            self._audio_refresh_deferred = True
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
        self.audio_error = None
        streams_changed = self.registry.refresh_streams(streams)
        outputs_changed = outputs != self.outputs
        self.outputs = outputs
        first_audio_snapshot = not self._audio_snapshot_ready
        self._audio_snapshot_ready = True
        if streams_changed or outputs_changed or first_audio_snapshot:
            self._emit_snapshot()
        self._emit_ready_if_synchronized()
        self._run_deferred_audio_refresh()

    def _audio_failed(self, message: str) -> None:
        self._audio_pending = False
        self.audio_error = message
        self.error_occurred.emit(message)
        self._run_deferred_audio_refresh()

    def _run_deferred_audio_refresh(self) -> None:
        if self._audio_refresh_deferred and not self._stopped:
            self._audio_refresh_deferred = False
            self.refresh_audio()

    def _window_event(self, event) -> None:
        if event.event_type in {WindowEventType.SNAPSHOT, WindowEventType.OUTPUT_CHANGED}:
            self.display_refresh_requested.emit()
        if event.event_type is WindowEventType.SNAPSHOT:
            self.registry.sync_windows(self.kwin.windows(include_all=True))
            self._window_snapshot_ready = True
        else:
            self.registry.handle_window_event(event)
        self._emit_snapshot()
        self._emit_ready_if_synchronized()

    def _emit_snapshot(self) -> None:
        self.snapshot_changed.emit(self.registry.entries, tuple(self.outputs))

    def _emit_ready_if_synchronized(self) -> None:
        if self._window_snapshot_ready and self._audio_snapshot_ready and not self._ready_emitted:
            self._ready_emitted = True
            self.ready.emit()

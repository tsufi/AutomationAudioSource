"""Non-blocking Qt adapter for explicit manual routing operations."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal

from screen_audio_router.audio.models import AudioOutput
from screen_audio_router.config.schema import RoutingMode
from screen_audio_router.router.application_registry import ApplicationEntry
from screen_audio_router.router.manual_routing import (
    ManualRoutingController,
    RoutingOperationResult,
)


class ManualRoutingService(QObject):
    completed = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, controller: ManualRoutingController, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="manual-routing")
        self._busy = False
        self._stopped = False

    @property
    def busy(self) -> bool:
        return self._busy

    def change_mode(
        self,
        application: ApplicationEntry,
        outputs: tuple[AudioOutput, ...],
        mode: RoutingMode,
    ) -> bool:
        return self._submit(self.controller.change_mode, application, outputs, mode)

    def select_fixed_output(
        self,
        application: ApplicationEntry,
        outputs: tuple[AudioOutput, ...],
        output_id: str,
    ) -> bool:
        return self._submit(self.controller.select_fixed_output, application, outputs, output_id)

    def apply_now(self, application: ApplicationEntry, outputs: tuple[AudioOutput, ...]) -> bool:
        return self._submit(self.controller.apply_now, application, outputs)

    def reset_configuration(
        self, application: ApplicationEntry, outputs: tuple[AudioOutput, ...]
    ) -> bool:
        return self._submit(self.controller.reset_configuration, application, outputs)

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _submit(self, operation, *args) -> bool:
        if self._busy or self._stopped:
            return False
        self._busy = True
        self.busy_changed.emit(True)
        future = self._executor.submit(operation, *args)
        future.add_done_callback(self._done)
        return True

    def _done(self, future: Future[RoutingOperationResult]) -> None:
        try:
            result = future.result()
        except (OSError, ValueError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - keep worker failures out of the Qt event loop
            self.failed.emit(f"Unexpected routing error: {exc}")
        else:
            self.completed.emit(result)
        finally:
            self._busy = False
            self.busy_changed.emit(False)

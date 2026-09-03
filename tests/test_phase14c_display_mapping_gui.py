import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QComboBox

from screen_audio_router.audio.models import AudioOutput, AudioStream, Availability
from screen_audio_router.config.schema import (
    AppConfig,
    ApplicationRule,
    AudioOutputIdentity,
    DisplayIdentity,
    DisplayMapping,
    RoutingMode,
)
from screen_audio_router.config.schema import (
    DisplayIdentityKind as ConfigIdentityKind,
)
from screen_audio_router.diagnostics import build_diagnostic_snapshot, render_diagnostic_report
from screen_audio_router.display import DisplayDevice, DisplayIdentityKind
from screen_audio_router.display_mapping import MappingOperationStatus, MigrationStatus
from screen_audio_router.display_runtime import DisplayRuntimeContext
from screen_audio_router.gui.application_list import ApplicationViewModel
from screen_audio_router.gui.runtime import GuiRuntime
from screen_audio_router.gui.settings_window import DisplayMappingSettingsDialog
from screen_audio_router.kwin.models import WindowInfo
from screen_audio_router.router.application_registry import ApplicationEntry, MatchedStream
from screen_audio_router.router.automatic_routing import AutomaticRoutingController
from screen_audio_router.router.engine import RouteExecutor
from screen_audio_router.router.matcher import ApplicationMatcher, MatchConfidence, MatchResult


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def device(
    display_id="edid_serial:tv",
    connector="DP-3",
    kind=DisplayIdentityKind.EDID_SERIAL,
    name="SAMSUNG",
):
    return DisplayDevice(
        display_id, kind, connector, True, name, "SAM", "TV", "123", "serial", True
    )


def output(output_id="hdmi", availability=Availability.AVAILABLE, name="Samsung HDMI"):
    return AudioOutput(output_id, name, availability)


def stable_mapping(output_id="hdmi"):
    return DisplayMapping(
        DisplayIdentity(
            "edid_serial:tv",
            friendly_name="SAMSUNG",
            identity_kind=ConfigIdentityKind.STABLE,
        ),
        AudioOutputIdentity(output_id, output_id, output_id.upper()),
    )


def fallback_mapping(output_id="gc7"):
    return DisplayMapping(
        DisplayIdentity(
            "DP-3",
            connector="DP-3",
            friendly_name="DP-3",
            identity_kind=ConfigIdentityKind.CONNECTOR_FALLBACK,
        ),
        AudioOutputIdentity(output_id, output_id, output_id.upper()),
    )


class Store:
    def __init__(self, fail=False):
        self.fail = fail
        self.saved = []

    def save(self, config):
        if self.fail:
            raise OSError("read only")
        self.saved.append(config.to_dict())


class Discovery:
    def __init__(self, snapshots=(), error=None):
        self.snapshots = list(snapshots)
        self.error = error

    def discover(self):
        if self.error:
            raise self.error
        return self.snapshots.pop(0) if self.snapshots else ()


def context(config=None, store=None, discovery=None):
    return DisplayRuntimeContext(
        config or AppConfig(), store or Store(), discovery or Discovery(((device(),),))
    )


def entry(connector="DP-3", app_id="auto"):
    window = WindowInfo("w", app_id, app_id, app_id, 1, connector, False, True, "normal")
    stream = AudioStream("1", application_name=app_id, current_output="gc7")
    matched = MatchedStream(stream, MatchResult(app_id, MatchConfidence.HIGH, "test", 90))
    return ApplicationEntry(
        app_id, app_id, (window,), (matched,), window, False, connector, ("gc7",)
    )


@pytest.mark.parametrize("snapshots,changed", [(((),), False), ((((device(),)),), True)])
def test_runtime_context_startup_snapshots(snapshots, changed):
    value = context(discovery=Discovery(snapshots))
    result = value.refresh()
    assert result.changed is changed


def test_discovery_failure_keeps_application_context_usable():
    value = context(discovery=Discovery(error=OSError("sysfs unavailable")))
    result = value.refresh()
    assert result.error == "sysfs unavailable"
    assert value.routing_mappings() == ()


def test_refresh_never_writes_configuration():
    store = Store()
    value = context(store=store)
    value.refresh()
    assert store.saved == []


@pytest.mark.parametrize(
    "mappings,connector,expected",
    [
        ((stable_mapping(),), "DP-3", "hdmi"),
        ((stable_mapping(),), "DP-1", "hdmi"),
        ((fallback_mapping(),), "DP-3", "gc7"),
        ((stable_mapping(), fallback_mapping()), "DP-3", "hdmi"),
        ((), "DP-3", None),
        ((stable_mapping("missing"),), "DP-3", "missing"),
    ],
)
def test_production_mapping_adapter_resolution(mappings, connector, expected):
    current = device(connector=connector)
    value = context(AppConfig(display_mappings=list(mappings)), discovery=Discovery(((current,),)))
    value.refresh()
    resolved = {item.display_id: item.output_id for item in value.routing_mappings()}
    assert resolved.get(connector) == expected


@pytest.mark.parametrize(
    "displays,mappings,expected_status",
    [
        ((device(),), (), "Not mapped"),
        ((device(),), (stable_mapping(),), "Physical display mapping"),
        (
            (device(kind=DisplayIdentityKind.CONNECTOR_FALLBACK),),
            (fallback_mapping(),),
            "Legacy connector mapping",
        ),
        ((), (stable_mapping(),), "Display disconnected — mapping retained"),
    ],
)
def test_settings_rows_render_mapping_states(qt_app, displays, mappings, expected_status):
    value = context(AppConfig(display_mappings=list(mappings)), discovery=Discovery((displays,)))
    value.refresh()
    dialog = DisplayMappingSettingsDialog(value)
    dialog.set_outputs((output(), output("gc7", name="GC7")))
    assert dialog.table.item(0, 3).text() == expected_status
    dialog.close()


@pytest.mark.parametrize(
    "availability,expected",
    [
        (Availability.AVAILABLE, "Physical display mapping"),
        (Availability.UNKNOWN, "Physical display mapping"),
        (Availability.UNAVAILABLE, "Mapped output unavailable"),
    ],
)
def test_settings_respects_output_availability(qt_app, availability, expected):
    value = context(AppConfig(display_mappings=[stable_mapping()]))
    value.refresh()
    dialog = DisplayMappingSettingsDialog(value)
    dialog.set_outputs((output(availability=availability),))
    assert dialog.table.item(0, 3).text() == expected
    dialog.close()


def test_settings_uses_friendly_output_choices(qt_app):
    value = context()
    value.refresh()
    dialog = DisplayMappingSettingsDialog(value)
    dialog.set_outputs((output(name="Friendly HDMI"),))
    selector = dialog.table.cellWidget(0, 2)
    assert isinstance(selector, QComboBox)
    assert selector.itemText(1) == "Friendly HDMI"
    dialog.close()


def test_output_popup_survives_refresh_and_commits_stable_id_once(qt_app):
    store = Store()
    value = context(store=store)
    value.refresh()
    dialog = DisplayMappingSettingsDialog(value)
    outputs = (output(), output("gc7", name="GC7"))
    dialog.set_outputs(outputs)
    dialog.show()
    selector = dialog.table.cellWidget(0, 2)
    selector.showPopup()
    qt_app.processEvents()

    dialog.set_outputs(outputs)

    assert dialog.table.cellWidget(0, 2) is selector
    assert selector.popup_visible is True
    assert store.saved == []

    selector.setCurrentIndex(selector.findData("gc7"))
    selector.activated.emit(selector.currentIndex())
    assert len(store.saved) == 1
    assert store.saved[0]["display_mappings"][0]["output"]["id"] == "gc7"

    selector.hidePopup()
    qt_app.processEvents()
    replacement = dialog.table.cellWidget(0, 2)
    assert replacement.currentData() == "gc7"
    assert len(store.saved) == 1
    dialog.close()


def test_changed_outputs_defer_until_popup_closes_and_preserve_stable_id(qt_app):
    store = Store()
    value = context(AppConfig(display_mappings=[stable_mapping()]), store)
    value.refresh()
    dialog = DisplayMappingSettingsDialog(value)
    dialog.set_outputs((output(),))
    dialog.show()
    selector = dialog.table.cellWidget(0, 2)
    selector.showPopup()

    dialog.set_outputs((output(name="Renamed HDMI"), output("gc7", name="GC7")))

    assert dialog.table.cellWidget(0, 2) is selector
    assert selector.popup_visible is True
    assert store.saved == []
    selector.hidePopup()
    qt_app.processEvents()
    replacement = dialog.table.cellWidget(0, 2)
    assert replacement.currentData() == "hdmi"
    assert replacement.currentText() == "Renamed HDMI"
    assert store.saved == []
    dialog.close()


@pytest.mark.parametrize(
    "output_id,status",
    [("hdmi", MappingOperationStatus.APPLIED), ("7", MappingOperationStatus.INVALID_OUTPUT)],
)
def test_settings_assignment_uses_typed_service(qt_app, output_id, status):
    store = Store()
    value = context(store=store)
    value.refresh()
    dialog = DisplayMappingSettingsDialog(value)
    dialog.set_outputs((output(output_id),))
    result = dialog.assign("edid_serial:tv", output_id)
    assert result.status is status
    assert all(
        item["output"]["id"] != "7" for saved in store.saved for item in saved["display_mappings"]
    )
    dialog.close()


def test_settings_assignment_failure_refreshes_prior_state(qt_app):
    config = AppConfig(display_mappings=[stable_mapping()])
    value = context(config, Store(fail=True))
    value.refresh()
    dialog = DisplayMappingSettingsDialog(value)
    dialog.set_outputs((output(), output("gc7", name="GC7")))
    result = dialog.assign("edid_serial:tv", "gc7")
    assert result.status is MappingOperationStatus.PERSISTENCE_ERROR
    assert config.display_mappings[0].output.id == "hdmi"
    dialog.close()


@pytest.mark.parametrize("connected", [True, False])
def test_settings_removes_connected_or_disconnected_mapping(qt_app, connected):
    config = AppConfig(display_mappings=[stable_mapping()])
    displays = (device(),) if connected else ()
    value = context(config, discovery=Discovery((displays,)))
    value.refresh()
    dialog = DisplayMappingSettingsDialog(value)
    assert dialog.remove("edid_serial:tv").status is MappingOperationStatus.APPLIED
    assert config.display_mappings == []
    dialog.close()


@pytest.mark.parametrize("confirmed", [False, True])
def test_migration_requires_explicit_confirmation(qt_app, confirmed):
    config = AppConfig(display_mappings=[fallback_mapping()])
    value = context(config)
    value.refresh()
    dialog = DisplayMappingSettingsDialog(value)
    result = dialog.migrate("DP-3", confirmed=confirmed)
    assert (result is not None) is confirmed
    assert (config.display_mappings[0].display.id == "edid_serial:tv") is confirmed
    dialog.close()


@pytest.mark.parametrize(
    "displays,mappings,status",
    [
        ((), (fallback_mapping(),), MigrationStatus.NO_CANDIDATE),
        ((device(), device("edid_serial:other")), (fallback_mapping(),), MigrationStatus.AMBIGUOUS),
        ((device(),), (fallback_mapping(), stable_mapping()), MigrationStatus.CONFLICT),
    ],
)
def test_migration_revalidates_conservative_states(displays, mappings, status):
    value = context(AppConfig(display_mappings=list(mappings)), discovery=Discovery((displays,)))
    value.refresh()
    assert value.service.preview_migration("DP-3", value.displays).status is status


class Backend:
    def __init__(self):
        self.moves = []

    def move_stream(self, stream_id, output_id):
        self.moves.append((stream_id, output_id))


class Handle:
    def cancel(self):
        pass


@pytest.mark.parametrize(
    "mode,connector,requested",
    [
        (RoutingMode.AUTO, "DP-3", ("auto",)),
        (RoutingMode.AUTO, "DP-2", ()),
        (RoutingMode.IGNORE, "DP-3", ()),
        (RoutingMode.FIXED_DEVICE, "DP-3", ()),
    ],
)
def test_targeted_reevaluation_filters_mode_and_display(mode, connector, requested):
    config = AppConfig(
        applications=[
            ApplicationRule(
                "auto",
                "Auto",
                mode,
                fixed_output_id="gc7" if mode is RoutingMode.FIXED_DEVICE else None,
            )
        ]
    )
    scheduled = []
    controller = AutomaticRoutingController(
        config,
        RouteExecutor(Backend()),
        schedule=lambda _delay, callback: scheduled.append(callback) or Handle(),
    )
    controller.update_snapshot((entry(),), (output(), output("gc7", name="GC7")))
    controller.mark_ready()
    scheduled.clear()
    result = controller.reevaluate_display(connector)
    assert result.requested == requested
    assert len(scheduled) == len(requested)
    controller.stop()


def test_application_view_model_consumes_resolved_mappings():
    config = AppConfig(
        applications=[ApplicationRule("auto", "Auto", RoutingMode.AUTO)],
        display_mappings=[stable_mapping()],
    )
    value = context(config)
    value.refresh()
    model = ApplicationViewModel(config, display_mappings=value.routing_mappings)
    model.update_runtime((entry(),), (output(), output("gc7", name="GC7")))
    assert model.rows()[0].decision.desired_output == "hdmi"


@pytest.mark.parametrize(
    "kind",
    [
        DisplayIdentityKind.EDID_SERIAL,
        DisplayIdentityKind.EDID_FINGERPRINT,
        DisplayIdentityKind.CONNECTOR_FALLBACK,
    ],
)
def test_diagnostics_report_identity_connection_and_source(kind):
    current = device(kind=kind)
    config = AppConfig(
        display_mappings=[
            fallback_mapping()
            if kind is DisplayIdentityKind.CONNECTOR_FALLBACK
            else stable_mapping()
        ]
    )
    value = context(config, discovery=Discovery(((current,),)))
    value.refresh()
    rows = value.service.rows(value.displays, (output(), output("gc7", name="GC7")))
    snapshot = build_diagnostic_snapshot(
        config,
        (),
        (output(), output("gc7", name="GC7")),
        display_rows=rows,
        display_discovery_health="Available",
        resolved_display_mappings=value.routing_mappings(),
    )
    report = render_diagnostic_report(snapshot)
    assert kind.value in report
    assert "Connected displays: 1" in report


def test_phase14c_gui_has_no_direct_audio_execution_or_config_list_mutation():
    source = (
        Path(__file__).parents[1] / "src/screen_audio_router/gui/settings_window.py"
    ).read_text()
    for forbidden in (
        "pactl",
        "subprocess",
        "RouteExecutor",
        "move_stream",
        "display_mappings.append",
        "display_mappings =",
    ):
        assert forbidden not in source


def test_runtime_context_has_no_qt_or_automatic_migration():
    source = (Path(__file__).parents[1] / "src/screen_audio_router/display_runtime.py").read_text()
    for forbidden in ("PySide6", "migrate_fallback", "migrate_mapping", ".save("):
        assert forbidden not in source


def test_settings_open_refresh_does_not_migrate_legacy_mapping(qt_app):
    config = AppConfig(display_mappings=[fallback_mapping()])
    store = Store()
    value = context(config, store)
    dialog = DisplayMappingSettingsDialog(value)
    dialog.refresh_displays()
    assert config.display_mappings[0].display.id == "DP-3"
    assert store.saved == []
    dialog.close()


def test_missing_mapped_output_remains_destination_missing_in_view_model():
    config = AppConfig(
        applications=[ApplicationRule("auto", "Auto", RoutingMode.AUTO)],
        display_mappings=[stable_mapping("missing")],
    )
    value = context(config)
    value.refresh()
    model = ApplicationViewModel(config, display_mappings=value.routing_mappings)
    model.update_runtime((entry(),), (output("gc7", name="GC7"),))
    assert model.rows()[0].status == "Device missing"


def test_auto_post_move_refresh_is_deferred_when_audio_poll_is_inflight(qt_app):
    runtime = GuiRuntime(ApplicationMatcher(), 1000)
    runtime._audio_pending = True
    runtime.refresh_audio()
    assert runtime._audio_refresh_deferred is True
    requested = []
    runtime.refresh_audio = lambda: requested.append(True)
    runtime._apply_audio_snapshot([], [])
    assert requested == [True]
    assert runtime._audio_refresh_deferred is False
    runtime.stop()


def test_runtime_ignores_refresh_requested_after_shutdown(qt_app):
    runtime = GuiRuntime(ApplicationMatcher(), 1000)
    runtime.stop()
    runtime.refresh_audio()
    assert runtime._audio_pending is False

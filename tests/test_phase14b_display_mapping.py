from pathlib import Path

import pytest

from screen_audio_router.audio.models import AudioOutput, AudioStream, Availability
from screen_audio_router.config.schema import (
    AppConfig,
    AudioOutputIdentity,
    DisplayIdentity,
    DisplayMapping,
    RoutingMode,
)
from screen_audio_router.config.schema import (
    DisplayIdentityKind as ConfigIdentityKind,
)
from screen_audio_router.display import DisplayDevice, DisplayIdentityKind
from screen_audio_router.display_mapping import (
    DisplayMappingService,
    MappingOperationStatus,
    MigrationStatus,
    RuntimeDisplayResolver,
    RuntimeResolutionKind,
)
from screen_audio_router.kwin.models import WindowInfo
from screen_audio_router.router.application_registry import ApplicationEntry, MatchedStream
from screen_audio_router.router.decisions import ApplicationRoutingRule, RouteAction, RouteReason
from screen_audio_router.router.engine import RouterEngine
from screen_audio_router.router.matcher import MatchConfidence, MatchResult


def device(stable_id="edid_serial:tv", connector="DP-3", kind=DisplayIdentityKind.EDID_SERIAL):
    return DisplayDevice(
        stable_id, kind, connector, True, "Television", "SAM", "TV", "1234", "SERIAL", True
    )


def output(output_id="hdmi", availability=Availability.AVAILABLE, name=None):
    return AudioOutput(output_id, name or output_id.upper(), availability)


def stable_mapping(display_id="edid_serial:tv", output_id="hdmi"):
    return DisplayMapping(
        DisplayIdentity(
            display_id, friendly_name="Television", identity_kind=ConfigIdentityKind.STABLE
        ),
        AudioOutputIdentity(output_id, output_id, output_id.upper()),
    )


def fallback_mapping(connector="DP-3", output_id="hdmi", *, prefixed=False):
    display_id = f"connector:{connector}" if prefixed else connector
    return DisplayMapping(
        DisplayIdentity(display_id, connector=connector, friendly_name=connector),
        AudioOutputIdentity(output_id, output_id, output_id.upper()),
    )


class Store:
    def __init__(self, fail=False):
        self.fail = fail
        self.saved = []

    def save(self, config):
        if self.fail:
            raise OSError("disk full")
        self.saved.append(config.to_dict())


def application(connector="DP-3", current="gc7"):
    window = WindowInfo("w", "app", "app", "App", 1, connector, False, True, "normal")
    stream = AudioStream("1", application_name="app", current_output=current)
    matched = MatchedStream(stream, MatchResult("app", MatchConfidence.HIGH, "test", 90))
    return ApplicationEntry(
        "app", "App", (window,), (matched,), window, False, connector, (current,)
    )


def decision(displays, mappings, connector="DP-3"):
    resolver = RuntimeDisplayResolver(tuple(displays))
    return RouterEngine().decide(
        application(connector),
        ApplicationRoutingRule("app", RoutingMode.AUTO),
        resolver.routing_mappings(AppConfig(display_mappings=list(mappings))),
        (output("gc7"), output("hdmi")),
    )


def test_runtime_connector_resolves_to_stable_identity():
    result = RuntimeDisplayResolver((device(),)).resolve("DP-3")
    assert (result.kind, result.display_id) == (
        RuntimeResolutionKind.RESOLVED_STABLE,
        "edid_serial:tv",
    )


def test_unknown_connector_is_unresolved_and_never_guesses():
    result = RuntimeDisplayResolver((device(), device("edid_serial:other", "DP-2"))).resolve("DP-9")
    assert result.kind is RuntimeResolutionKind.UNRESOLVED
    assert result.display_id is None


def test_connector_fallback_device_resolves_as_fallback():
    fallback = device("connector:DP-3", "DP-3", DisplayIdentityKind.CONNECTOR_FALLBACK)
    assert (
        RuntimeDisplayResolver((fallback,)).resolve("DP-3").kind
        is RuntimeResolutionKind.RESOLVED_CONNECTOR_FALLBACK
    )


def test_stable_mapping_lookup_and_router_integration():
    result = decision((device(),), (stable_mapping(),))
    assert (result.action, result.desired_output) == (RouteAction.MOVE, "hdmi")


def test_same_stable_mapping_survives_connector_change_without_config_rewrite():
    mapping = stable_mapping()
    before = AppConfig(display_mappings=[mapping]).to_dict()
    result = decision((device(connector="DP-1"),), (mapping,), "DP-1")
    assert result.desired_output == "hdmi"
    assert AppConfig(display_mappings=[mapping]).to_dict() == before


@pytest.mark.parametrize("prefixed", [False, True])
def test_legacy_connector_mapping_remains_router_compatible(prefixed):
    result = decision((device(),), (fallback_mapping(prefixed=prefixed),))
    assert result.desired_output == "hdmi"


def test_stable_mapping_wins_over_conflicting_fallback():
    result = decision((device(),), (stable_mapping(), fallback_mapping(output_id="gc7")))
    assert result.desired_output == "hdmi"


def test_no_stable_mapping_uses_explicit_fallback_only():
    resolver = RuntimeDisplayResolver((device(),))
    config = AppConfig(display_mappings=[fallback_mapping(output_id="gc7")])
    assert resolver.resolved_mapping("DP-3", config).output_id == "gc7"
    assert resolver.resolved_mapping("DP-9", config) is None


def test_no_mapping_reaches_display_unmapped():
    result = decision((device(),), ())
    assert (result.action, result.reason) == (RouteAction.NO_ACTION, RouteReason.DISPLAY_UNMAPPED)


def test_rows_combine_connected_and_legacy_mapping_without_duplicate():
    config = AppConfig(display_mappings=[fallback_mapping()])
    store = Store()
    rows = DisplayMappingService(config, store).rows((device(),), (output(),))
    assert len(rows) == 1
    assert (rows[0].connected, rows[0].mapped_output_id) == (True, "hdmi")
    assert store.saved == []


def test_disconnected_mapping_and_friendly_metadata_are_retained():
    rows = DisplayMappingService(AppConfig(display_mappings=[stable_mapping()]), Store()).rows(
        (), ()
    )
    assert (rows[0].connected, rows[0].friendly_name, rows[0].mapped_output_name) == (
        False,
        "Television",
        "HDMI",
    )


@pytest.mark.parametrize(
    "availability,expected",
    [
        (Availability.AVAILABLE, True),
        (Availability.UNKNOWN, True),
        (Availability.UNAVAILABLE, False),
    ],
)
def test_output_availability_respects_explicit_backend_state(availability, expected):
    rows = DisplayMappingService(AppConfig(display_mappings=[stable_mapping()]), Store()).rows(
        (device(),), (output(availability=availability, name="Friendly HDMI"),)
    )
    assert rows[0].output_available is expected


def test_absent_mapped_output_is_retained_unavailable():
    row = DisplayMappingService(AppConfig(display_mappings=[stable_mapping()]), Store()).rows(
        (device(),), ()
    )[0]
    assert row.mapped_output_id == "hdmi"
    assert row.output_available is False


def test_assign_and_update_stable_mapping_with_audio_metadata():
    config, store = AppConfig(), Store()
    service = DisplayMappingService(config, store)
    first = service.assign_output("edid_serial:tv", "hdmi", (device(),), (output(name="Friendly"),))
    second = service.assign_output(
        "edid_serial:tv", "gc7", (device(),), (output("gc7", name="GC7"),)
    )
    assert first.status is second.status is MappingOperationStatus.APPLIED
    assert config.display_mappings[0].display.connector is None
    assert config.display_mappings[0].output.description == "GC7"
    assert config.display_mappings[0].output.id == "gc7"
    assert len(store.saved) == 2


@pytest.mark.parametrize(
    "display_id,output_id,status",
    [
        ("missing", "hdmi", MappingOperationStatus.INVALID_DISPLAY),
        ("edid_serial:tv", "42", MappingOperationStatus.INVALID_OUTPUT),
    ],
)
def test_invalid_assignment_is_not_persisted(display_id, output_id, status):
    store = Store()
    result = DisplayMappingService(AppConfig(), store).assign_output(
        display_id, output_id, (device(),), (output(output_id),)
    )
    assert result.status is status
    assert store.saved == []


def test_remove_is_scoped_and_idempotent():
    other = stable_mapping("edid_serial:other", "gc7")
    config, store = AppConfig(display_mappings=[stable_mapping(), other]), Store()
    service = DisplayMappingService(config, store)
    assert service.remove_mapping("edid_serial:tv").status is MappingOperationStatus.APPLIED
    assert [item.display.id for item in config.display_mappings] == ["edid_serial:other"]
    assert service.remove_mapping("edid_serial:tv").status is MappingOperationStatus.NO_CHANGE
    assert len(store.saved) == 1


@pytest.mark.parametrize("operation", ["assign", "remove"])
def test_persistence_failure_restores_previous_effective_state(operation):
    config = AppConfig(display_mappings=[stable_mapping()])
    original = config.to_dict()
    service = DisplayMappingService(config, Store(fail=True))
    result = (
        service.assign_output("edid_serial:tv", "gc7", (device(),), (output("gc7"),))
        if operation == "assign"
        else service.remove_mapping("edid_serial:tv")
    )
    assert result.status is MappingOperationStatus.PERSISTENCE_ERROR
    assert config.to_dict() == original


def test_safe_migration_preview_and_apply_is_single_transaction():
    config, store = AppConfig(display_mappings=[fallback_mapping()]), Store()
    service = DisplayMappingService(config, store)
    preview = service.preview_migration("DP-3", (device(),))
    assert (preview.status, preview.target_display_id, preview.output_id) == (
        MigrationStatus.MIGRATED,
        "edid_serial:tv",
        "hdmi",
    )
    result = service.migrate_fallback("DP-3", (device(),))
    assert result.status is MigrationStatus.MIGRATED
    assert [(item.display.id, item.output.id) for item in config.display_mappings] == [
        ("edid_serial:tv", "hdmi")
    ]
    assert len(store.saved) == 1


def test_same_output_existing_stable_removes_proven_duplicate():
    config = AppConfig(display_mappings=[fallback_mapping(), stable_mapping()])
    service = DisplayMappingService(config, Store())
    preview = service.preview_migration("DP-3", (device(),))
    assert preview.removes_duplicate is True
    service.migrate_fallback("DP-3", (device(),))
    assert [item.display.id for item in config.display_mappings] == ["edid_serial:tv"]


def test_conflicting_migration_changes_nothing():
    config = AppConfig(display_mappings=[fallback_mapping(output_id="gc7"), stable_mapping()])
    original = config.to_dict()
    result = DisplayMappingService(config, Store()).migrate_fallback("DP-3", (device(),))
    assert result.status is MigrationStatus.CONFLICT
    assert config.to_dict() == original


def test_ambiguous_or_fallback_only_candidate_does_not_migrate():
    config = AppConfig(display_mappings=[fallback_mapping()])
    service = DisplayMappingService(config, Store())
    duplicate_connector = (device(), device("edid_serial:other", "DP-3"))
    assert (
        service.preview_migration("DP-3", duplicate_connector).status is MigrationStatus.AMBIGUOUS
    )
    fallback = device("connector:DP-3", "DP-3", DisplayIdentityKind.CONNECTOR_FALLBACK)
    assert service.preview_migration("DP-3", (fallback,)).status is MigrationStatus.NO_CANDIDATE


def test_migration_persistence_failure_preserves_fallback():
    config = AppConfig(display_mappings=[fallback_mapping()])
    original = config.to_dict()
    result = DisplayMappingService(config, Store(fail=True)).migrate_fallback("DP-3", (device(),))
    assert result.status is MigrationStatus.PERSISTENCE_ERROR
    assert config.to_dict() == original


def test_service_has_no_gui_execution_or_audio_mutation_dependencies():
    source = Path(
        __import__(
            "screen_audio_router.display_mapping", fromlist=["DisplayMappingService"]
        ).__file__
    ).read_text()
    for forbidden in ("PySide6", "RouteExecutor", "move_stream", "set_default", "set_card_profile"):
        assert forbidden not in source


def test_production_runtime_does_not_import_phase14_modules():
    root = Path(__file__).parents[1] / "src" / "screen_audio_router"
    phase14 = {"display.py", "display_mapping.py"}
    consumers = [
        path
        for path in root.rglob("*.py")
        if path.name not in phase14
        and (
            "screen_audio_router.display" in path.read_text()
            or "screen_audio_router.display_mapping" in path.read_text()
        )
    ]
    assert {path.relative_to(root).as_posix() for path in consumers} <= {
        "diagnostics.py",
        "display_runtime.py",
        "gui/main_window.py",
        "gui/settings_window.py",
    }

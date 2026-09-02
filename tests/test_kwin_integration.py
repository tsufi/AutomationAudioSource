import json

import pytest

from screen_audio_router.kwin import KWinIntegration, KWinPayloadError, WindowEventType, WindowInfo


def payload(runtime_id="one", **changes):
    value = {
        "runtimeId": runtime_id,
        "resourceClass": "example.app",
        "resourceName": "example",
        "caption": "Example",
        "pid": 123,
        "outputName": "DP-1",
        "active": False,
        "normalWindow": True,
        "windowType": "normal",
    }
    value.update(changes)
    return value


def test_window_info_serialization_round_trip():
    window = WindowInfo.from_dict(payload())
    restored = WindowInfo(**window.to_dict())
    assert restored == window
    assert restored.runtime_id == "one"


def test_snapshot_and_independent_multiple_windows():
    integration = KWinIntegration()
    integration.handle_json_event("snapshot", json.dumps([payload("one"), payload("two")]))
    assert {window.runtime_id for window in integration.windows()} == {"one", "two"}


def test_update_and_output_change_preserve_runtime_identity():
    integration = KWinIntegration()
    events = []
    integration.add_listener(events.append)
    integration.handle_json_event("added", json.dumps(payload()))
    integration.handle_json_event("output_changed", json.dumps(payload(outputName="DP-2")))
    assert integration.windows()[0].output_name == "DP-2"
    assert events[-1].event_type is WindowEventType.OUTPUT_CHANGED
    assert events[-1].old_output == "DP-1"


def test_removal():
    integration = KWinIntegration()
    integration.handle_json_event("added", json.dumps(payload()))
    integration.handle_json_event("removed", json.dumps({"runtimeId": "one"}))
    assert integration.windows() == []


def test_activation_updates_all_windows():
    integration = KWinIntegration()
    integration.handle_json_event("snapshot", json.dumps([payload("one"), payload("two")]))
    integration.handle_json_event("activated", json.dumps(payload("two", active=True)))
    state = {window.runtime_id: window.active for window in integration.windows()}
    assert state == {"one": False, "two": True}


def test_default_view_filters_but_all_preserves_raw_windows():
    integration = KWinIntegration()
    integration.handle_json_event("snapshot", json.dumps([payload(normalWindow=False)]))
    assert integration.windows() == []
    assert len(integration.windows(include_all=True)) == 1


@pytest.mark.parametrize(
    ("event_name", "raw"),
    [
        ("unknown", "{}"),
        ("added", "not-json"),
        ("added", "{}"),
        ("snapshot", "{}"),
        ("removed", "{}"),
        ("added", json.dumps(payload(pid="wrong"))),
    ],
)
def test_malformed_events_are_rejected(event_name, raw):
    with pytest.raises(KWinPayloadError):
        KWinIntegration().handle_json_event(event_name, raw)

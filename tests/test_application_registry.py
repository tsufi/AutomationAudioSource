from screen_audio_router.audio.models import AudioStream
from screen_audio_router.kwin.integration import WindowEvent, WindowEventType
from screen_audio_router.kwin.models import WindowInfo
from screen_audio_router.router.application_registry import ApplicationRegistry
from screen_audio_router.router.matcher import AliasRule, ApplicationMatcher, MatchConfidence


def window(runtime_id, app_id="brave-browser", output="DP-2", active=False, pid=100):
    return WindowInfo(
        runtime_id=runtime_id,
        resource_class=app_id,
        resource_name="brave" if app_id == "brave-browser" else "plezy",
        caption=runtime_id,
        pid=pid,
        output_name=output,
        active=active,
        normal_window=True,
        window_type="normal",
    )


def registry():
    aliases = (AliasRule("com.edde746.plezy", ("mpv",), ("mpv",)),)
    return ApplicationRegistry(ApplicationMatcher(aliases))


def test_multiple_windows_group_under_stable_identity():
    value = registry()
    value.sync_windows([window("a"), window("b", output="DP-3")])
    assert len(value.entries) == 1
    assert value.entries[0].application_id == "brave-browser"
    assert {item.runtime_id for item in value.entries[0].windows} == {"a", "b"}


def test_controlling_window_policy_tracks_activation():
    value = registry()
    a = window("a", output="DP-2")
    b = window("b", output="DP-3")
    value.sync_windows([a, b])
    value.handle_window_event(WindowEvent(WindowEventType.ACTIVATED, b.__class__(**(b.to_dict() | {"active": True}))))
    assert value.entries[0].controlling_window.runtime_id == "b"
    assert value.entries[0].current_display == "DP-3"
    value.handle_window_event(WindowEvent(WindowEventType.ACTIVATED, a.__class__(**(a.to_dict() | {"active": True}))))
    assert value.entries[0].controlling_window.runtime_id == "a"
    assert value.entries[0].current_display == "DP-2"
    assert [item.runtime_id for item in value.entries[0].windows if item.active] == ["a"]


def test_activation_clears_active_state_from_other_application():
    value = registry()
    first = window("a", active=True)
    second = window("b", app_id="code", pid=200)
    value.sync_windows([first, second])
    value.handle_window_event(
        WindowEvent(WindowEventType.ACTIVATED, second.__class__(**(second.to_dict() | {"active": True})))
    )
    assert [entry.application_id for entry in value.entries if entry.active] == ["code"]


def test_window_removal_and_window_only_application():
    value = registry()
    a = window("a")
    value.sync_windows([a])
    assert value.entries[0].streams == ()
    value.handle_window_event(WindowEvent(WindowEventType.REMOVED, a))
    assert value.entries == ()


def test_stream_appearance_update_and_disappearance():
    value = registry()
    value.sync_windows([window("a")])
    value.refresh_streams([AudioStream("1", application_name="brave-browser", current_output="sink.one")])
    assert value.entries[0].streams[0].stream.current_output == "sink.one"
    value.refresh_streams([AudioStream("1", application_name="brave-browser", current_output="sink.two")])
    assert value.entries[0].current_audio_outputs == ("sink.two",)
    value.refresh_streams([])
    assert value.entries[0].streams == ()


def test_audio_only_application_is_preserved():
    value = registry()
    value.refresh_streams([AudioStream("4", application_name="background-player")])
    assert value.entries[0].application_id == "audio:background-player"
    assert value.entries[0].audio_only is True
    assert value.entries[0].windows == ()


def test_single_plezy_alias_stream_matches_high():
    value = registry()
    value.sync_windows([window("p", "com.edde746.plezy", pid=5776)])
    value.refresh_streams([AudioStream("327", application_name="mpv")])
    match = value.entries[0].streams[0].match
    assert match.confidence is MatchConfidence.HIGH
    assert match.reason == "configured alias"


def test_two_indistinguishable_mpv_streams_remain_unmatched():
    value = registry()
    value.sync_windows([window("p", "com.edde746.plezy", pid=5776)])
    value.refresh_streams(
        [AudioStream("327", application_name="mpv"), AudioStream("328", application_name="mpv")]
    )
    assert value.entries[0].streams == ()
    assert len(value.unmatched_streams) == 2
    assert all("ambiguous" in item.reason for item in value.unmatched_streams)


def test_pid_disambiguates_one_mpv_and_leaves_other_unmatched():
    value = registry()
    value.sync_windows([window("p", "com.edde746.plezy", pid=5776)])
    value.refresh_streams(
        [
            AudioStream("327", application_name="mpv", process_id=5776),
            AudioStream("328", application_name="mpv", process_id=9999),
        ]
    )
    assert [item.stream.stream_id for item in value.entries[0].streams] == ["327"]
    assert value.entries[0].streams[0].match.confidence is MatchConfidence.EXACT
    assert [item.stream.stream_id for item in value.unmatched_streams] == ["328"]

# Screen Audio Router

## DESIGN.md

Version: 0.1  
Status: Initial architecture / implementation guide  
Primary platform: CachyOS / Arch Linux  
Desktop target: KDE Plasma 6 + KWin + Wayland  
Audio target: PipeWire with PulseAudio compatibility  
Primary UI toolkit: Qt 6  
Recommended implementation language: Python 3 + PySide6 for V1

---

# 1. Product Summary

Screen Audio Router is a Linux desktop application for automatically routing application-specific audio streams to audio outputs based on the physical display where the application's window is located.

The application must not change the global/default system audio output during normal routing.

Instead, it manages individual application audio streams.

Primary use case:

- Plezy is on the Samsung TV -> Plezy audio goes to Samsung TV HDMI.
- Plezy is moved back to the PC monitor -> Plezy audio goes to Sound Blaster GC7.
- Discord, games, browsers, music players, and other applications keep their own independent routing rules.
- The user can override automatic routing at any time.
- Automatic routing must never fight an active manual override.

The long-term goal is a general-purpose KDE application that makes application-level PipeWire routing easy to manage without requiring terminal commands.

---

# 2. Core Design Principles

## 2.1 Application-first UI

The normal user should think in terms of applications:

- Plezy
- Brave
- Discord
- VLC
- Spotify
- Steam

The normal UI should not expose:

- sink-input numeric IDs
- PipeWire node IDs
- KWin internals
- resourceClass values
- raw process metadata

Those values may be exposed in an Advanced / Diagnostics view.

---

## 2.2 Safe by default

Unknown applications default to:

    IGNORE

The router must never unexpectedly take control of a newly launched application's audio.

The user explicitly changes an application to:

    AUTO
    specific device
    IGNORE

---

## 2.3 Manual choice always wins

When an application is in AUTO mode, the router may move its audio according to display mappings.

When the user selects a specific audio output manually, automatic screen-following must stop for that application.

Example:

    Plezy window is on TV.
    AUTO would use Samsung TV audio.

User selects:

    Sound Blaster GC7

Result:

    video remains on TV
    audio remains on GC7

Moving the Plezy window must not change the audio while the manual device selection is active.

When the user selects AUTO again, the router immediately applies the current screen mapping.

---

## 2.4 No global output switching

Normal routing must operate on per-application audio streams.

Avoid:

    pactl set-default-sink

for normal application routing.

Use stream movement instead.

V1 may use:

    pactl move-sink-input

behind a clean backend abstraction.

---

## 2.5 Wayland-native design

The target environment is KDE Plasma on Wayland.

Do not use X11-only tools as the core implementation:

- xdotool
- xprop
- wmctrl

Window tracking should use KWin's supported scripting / DBus integration.

---

## 2.6 Replaceable backends

Do not couple the GUI directly to `pactl`.

Architecture must allow the initial PulseAudio-compatible backend to later be replaced with a native PipeWire backend.

Likewise, KWin integration must be isolated from routing logic.

---

# 3. Initial Target Environment

Known working development machine:

    OS: CachyOS x86_64
    Desktop: KDE Plasma 6
    Window Manager: KWin Wayland
    Audio: PipeWire
    GPU: NVIDIA GeForce RTX 3080

Known displays:

    Samsung 4K TV
    Samsung Odyssey G5
    ASUS VP278

Known current KWin connectors:

    Samsung TV: DP-3
    Odyssey G5: DP-2
    ASUS VP278: HDMI-A-1

Connector names must not be treated as permanent stable identities.

Known current PC audio output:

    Sound Blaster GC7

Known current TV audio output:

    alsa_output.pci-0000_01_00.1.hdmi-stereo-extra2

Known NVIDIA audio card:

    alsa_card.pci-0000_01_00.1

Known Samsung HDMI profile:

    output:hdmi-stereo-extra2

All device values must be discovered dynamically by the final application.

---

# 4. Existing Proven Prototype

A working Plezy-specific prototype already exists.

KWin identifies Plezy as:

    resourceClass = com.edde746.plezy
    resourceName  = plezy
    caption       = Plezy

Plezy playback currently creates an audio stream that can be matched using:

    mpv

The following routing behavior has been proven to work:

TV:

    pactl move-sink-input <STREAM_ID> \
      alsa_output.pci-0000_01_00.1.hdmi-stereo-extra2

PC:

    pactl move-sink-input <STREAM_ID> \
      alsa_output.usb-Creative_Technology_Ltd_Sound_Blaster_GC7_EACA2388E6EA4D23-03.analog-stereo

NVIDIA HDMI output may require:

    pactl set-card-profile \
      alsa_card.pci-0000_01_00.1 \
      output:hdmi-stereo-extra2

Existing Plezy prototype files may include:

    ~/.local/bin/plezy-audio-route

    ~/.config/systemd/user/plezy-audio-route@.service
    ~/.config/systemd/user/plezy-audio-apply.service
    ~/.config/systemd/user/plezy-audio-apply.timer

    ~/.local/share/kwin/scripts/plezy-audio-router/

These must remain untouched until the new application passes integration tests.

---

# 5. Main User Experience

The primary window should look conceptually like:

    Screen Audio Router
    ----------------------------------------------------------

    Router: ● Active

    ACTIVE AUDIO

    Plezy
    Screen: Samsung TV
    Audio: Samsung TV
    Routing: AUTO
    [ AUTO ▼ ]

    Brave
    Screen: Odyssey G5
    Audio: Sound Blaster GC7
    Routing: AUTO
    [ AUTO ▼ ]

    Discord
    Screen: Odyssey G5
    Audio: Sound Blaster GC7
    Routing: Sound Blaster GC7
    [ Sound Blaster GC7 ▼ ]

    ----------------------------------------------------------

    OTHER APPLICATIONS

    Steam
    Screen: Odyssey G5
    Audio: No active audio
    Routing: IGNORE
    [ IGNORE ▼ ]

The dropdown for each application should contain:

    Auto (Follow Screen)
    --------------------
    Sound Blaster GC7
    Samsung TV
    any other currently available output
    --------------------
    Ignore

Audio output options must be generated dynamically.

---

# 6. Routing Modes

Each application rule has one primary routing mode.

## 6.1 IGNORE

Router performs no actions for this application.

System/PipeWire behavior remains untouched.

This is the default for newly discovered applications.

---

## 6.2 FOLLOW_SCREEN / AUTO

Application audio follows the configured audio mapping for the display containing the relevant application window.

Example:

    Plezy on Odyssey G5
        -> GC7

    Plezy moved to Samsung TV
        -> Samsung TV HDMI

    Plezy moved back
        -> GC7

---

## 6.3 FIXED_DEVICE

Application audio is always routed to a selected device.

Example:

    Discord -> Sound Blaster GC7

Window location has no effect.

---

# 7. Temporary vs Persistent Manual Selection

For V1, selecting a specific device from the main application row should behave as a persistent FIXED_DEVICE rule.

Future enhancement:

    temporary override until:
        application closes
        session ends
        user selects AUTO
        configurable timeout

Possible future menu:

    Auto
    Use GC7
    Use Samsung TV
    Temporary -> GC7
    Temporary -> Samsung TV
    Ignore

Do not implement this complexity before the basic persistent model is stable.

---

# 8. Display Mapping Model

AUTO routing requires a mapping:

    physical display -> preferred audio device

Example:

    Samsung TV
        -> Samsung HDMI audio

    Odyssey G5
        -> Sound Blaster GC7

    ASUS VP278
        -> Sound Blaster GC7

The mapping should be editable in Settings.

---

# 9. Display Identity

Avoid storing only:

    DP-3

because connector names may change.

Store the richest available display identity:

    connector
    manufacturer
    model
    serial
    UUID
    EDID-related stable identity when available

Suggested model:

```json
{
  "id": "display-samsung-tv",
  "connector": "DP-3",
  "manufacturer": "Samsung",
  "model": "SAMSUNG",
  "serial": "",
  "uuid": "...",
  "friendlyName": "Samsung TV"
}
```

Resolution strategy after reboot/reinstall:

1. exact stable UUID / serial match
2. manufacturer + model + serial
3. manufacturer + model
4. connector fallback
5. mark mapping unresolved instead of guessing

Never silently map an unresolved TV rule to an arbitrary monitor.

---

# 10. Audio Device Identity

Persistent configuration must not use numeric sink IDs.

Bad:

    54
    883

Good:

    alsa_output....

Store useful metadata:

    sink name
    description
    device bus
    card name
    profile
    device strings

Suggested model:

```json
{
  "id": "audio-gc7",
  "sinkName": "alsa_output.usb-....analog-stereo",
  "description": "Sound Blaster GC7 Analog Stereo",
  "cardName": "alsa_card.usb-...",
  "profile": "output:analog-stereo"
}
```

Audio device resolver should attempt to reconnect saved rules after devices disappear/reappear.

---

# 11. Application Identity

An application is not the same thing as an audio stream.

The router should build an ApplicationModel using data from both KWin and PipeWire/PulseAudio.

Useful KWin fields:

    resourceClass
    resourceName
    caption
    pid
    output

Useful audio fields:

    application.name
    application.process.binary
    application.process.id
    application.process.machine_id
    media.name
    sink
    stream ID

Suggested application key priority:

1. explicit saved application rule ID
2. resourceClass
3. desktop file / application ID if available
4. process binary
5. fallback composite identity

---

# 12. Special Application Audio Matching

Some applications create audio through another process.

Known example:

    Plezy window:
        com.edde746.plezy

    Plezy audio:
        mpv

Therefore application rules must support separate:

    window matching
    audio matching

Example:

```json
{
  "name": "Plezy",
  "windowMatch": {
    "resourceClass": "com.edde746.plezy"
  },
  "audioMatch": {
    "processBinary": "mpv"
  }
}
```

The matching editor should eventually support:

    processBinary
    applicationName
    PID relationship
    mediaName
    custom contains / equals match

V1 only needs enough flexibility to support known real cases.

---

# 13. Multiple Streams

One application may create several streams.

Example:

    Brave
        stream 421
        stream 425
        stream 430

Normal UI should show one application:

    Brave
    Audio: Samsung TV
    AUTO

Routing a managed application should move every audio stream that belongs to the application's matching rule.

Advanced diagnostics may expose individual streams.

---

# 14. Multiple Windows

One application may have multiple windows on different monitors.

This creates an ambiguity because one group of audio streams may not map cleanly to one specific window.

V1 behavior must be deterministic.

Recommended V1 policy:

    Use the most recently active/relevant normal window for the application.

Priority:

1. currently active application window
2. most recently moved application window
3. most recently activated normal window
4. first valid normal window fallback

Store/display which window currently controls AUTO routing.

UI example:

    Brave
    Auto source window: YouTube - Brave
    Screen: Samsung TV

Future versions may implement per-window stream matching where process metadata allows it.

---

# 15. Application Discovery

The main UI automatically discovers applications.

Do not require a separate Add Application flow for normal use.

Combine:

    KWin windows
    processes
    active audio streams

Applications should appear even without audio if they have a visible KWin window.

Suggested sections:

    ACTIVE AUDIO
    OTHER APPLICATIONS

Applications with active audio move automatically into ACTIVE AUDIO.

Unknown applications start with:

    IGNORE

---

# 16. Router Architecture

Recommended high-level architecture:

```text
                  KWin Script
                      |
                    DBus
                      |
                      v
              KWinIntegration
                      |
                      v
                RouterEngine
              /      |       \
             /       |        \
       AppRegistry  Config   AudioBackend
             |                   |
             |                 pactl
             |
             v
          GUI Model
             |
        Qt / PySide6 UI
```

The background router must not depend on the main window being visible.

---

# 17. Recommended Source Tree

```text
screen-audio-router/
├── README.md
├── DESIGN.md
├── pyproject.toml
├── requirements.txt
│
├── src/
│   └── screen_audio_router/
│       ├── __init__.py
│       ├── main.py
│       │
│       ├── audio/
│       │   ├── __init__.py
│       │   ├── backend.py
│       │   ├── pactl_backend.py
│       │   ├── models.py
│       │   └── parser.py
│       │
│       ├── kwin/
│       │   ├── __init__.py
│       │   ├── integration.py
│       │   ├── models.py
│       │   └── dbus_service.py
│       │
│       ├── router/
│       │   ├── __init__.py
│       │   ├── engine.py
│       │   ├── application_registry.py
│       │   ├── matcher.py
│       │   └── decisions.py
│       │
│       ├── config/
│       │   ├── __init__.py
│       │   ├── manager.py
│       │   ├── schema.py
│       │   └── migration.py
│       │
│       ├── gui/
│       │   ├── __init__.py
│       │   ├── main_window.py
│       │   ├── application_list.py
│       │   ├── settings_window.py
│       │   ├── diagnostics.py
│       │   └── tray.py
│       │
│       └── logging/
│           ├── __init__.py
│           └── setup.py
│
├── kwin/
│   └── screen-audio-router/
│       ├── metadata.json
│       └── contents/
│           └── code/
│               └── main.js
│
├── systemd/
│   └── screen-audio-router.service
│
├── tests/
│   ├── test_audio_parser.py
│   ├── test_matcher.py
│   ├── test_router.py
│   └── fixtures/
│
└── scripts/
    ├── install-user.sh
    ├── uninstall-user.sh
    └── migrate-plezy-router.sh
```

Exact structure may evolve, but component boundaries should remain.

---

# 18. AudioBackend Interface

Create a backend interface independent of the GUI.

Example conceptual API:

```python
class AudioBackend:
    def list_outputs(self) -> list[AudioOutput]:
        ...

    def list_streams(self) -> list[AudioStream]:
        ...

    def move_stream(self, stream_id: str, output_id: str) -> None:
        ...

    def get_stream_output(self, stream_id: str) -> str | None:
        ...

    def ensure_output_available(self, output_id: str) -> bool:
        ...

    def set_card_profile(self, card_id: str, profile: str) -> None:
        ...
```

V1 implementation:

    PactlAudioBackend

Future:

    NativePipeWireAudioBackend

No GUI code should directly run `pactl`.

---

# 19. KWin Integration

A small KWin JavaScript component should track windows and send events to the application.

Required events:

    windowAdded
    windowRemoved
    windowActivated
    windowOutputChanged

Required metadata:

    resourceClass
    resourceName
    caption
    pid
    output identity

KWin script should be intentionally small.

It should not contain routing policy.

Bad:

    KWin script decides GC7 vs TV.

Good:

    KWin script reports:
        Plezy changed DP-2 -> DP-3

RouterEngine decides the routing.

---

# 20. DBus Integration

Preferred IPC:

    KWin script
        -> DBus
        -> screen-audio-router background process

The background process may expose a user-session DBus service such as:

    org.screenaudiorouter.Service

Possible methods/events:

    WindowAdded
    WindowRemoved
    WindowUpdated
    WindowActivated

The exact interface can be adjusted during implementation.

No root/system DBus service is required.

---

# 21. RouterEngine Responsibilities

RouterEngine owns routing policy.

Input:

    application state
    windows
    active streams
    display mappings
    saved application rules
    device availability

Output:

    RouteDecision

Example:

```text
Application: Plezy
Rule: AUTO
Controlling window: Plezy
Display: Samsung TV
Mapped output: Samsung TV HDMI
Streams: 327
Decision: move 327 -> Samsung HDMI
```

RouterEngine must not directly update widgets.

It emits state/events consumed by GUI models.

---

# 22. Routing Decision Rules

Suggested priority:

1. Router globally disabled
       -> do nothing

2. Application mode IGNORE
       -> do nothing

3. Application mode FIXED_DEVICE
       -> use configured device

4. Application mode AUTO
       -> resolve controlling window
       -> resolve display
       -> resolve display mapping
       -> use mapped audio output

5. Destination unavailable
       -> do not guess
       -> mark DEVICE_MISSING
       -> retry when devices change

6. Already routed correctly
       -> no action

7. Otherwise
       -> move matching streams

---

# 23. New Stream Handling

The router must handle:

    window already on TV
    no audio currently playing
    user starts playback
    new audio stream appears

Do not rely only on window movement.

V1 acceptable strategy:

    poll audio streams every 1-2 seconds

This is proven simple and should have negligible impact at this scale.

Architecture should permit replacing polling with event-driven PipeWire monitoring later.

Important:

Polling must only inspect state and perform routing when necessary.

Do not repeatedly move streams that are already on the correct output.

---

# 24. Main Window

Recommended components:

Top bar:

    Screen Audio Router
    Router Enabled toggle
    Settings
    Diagnostics

Main list columns/cards:

    Application
    Current Screen
    Current Audio
    Routing
    Status

Possible statuses:

    AUTO
    MANUAL
    IGNORED
    NO AUDIO
    DEVICE MISSING
    NO DISPLAY MAPPING
    ROUTING ERROR

---

# 25. Routing Selector

Each application row includes one control:

    [ AUTO ▼ ]

Available options dynamically:

    Auto (Follow Screen)
    Sound Blaster GC7
    Samsung TV
    Built-in Audio
    ...
    Ignore

Selecting AUTO:

    save AUTO
    immediately route according to current display

Selecting device:

    save FIXED_DEVICE
    immediately move active streams there

Selecting IGNORE:

    save IGNORE
    perform no further moves

---

# 26. System Tray

The application should support system tray operation.

Main window may be closed while router remains active.

Tray menu concept:

```text
Screen Audio Router
-------------------------
Router Enabled ✓

Plezy
  Auto ✓
  Sound Blaster GC7
  Samsung TV

Brave
  Auto ✓
  Sound Blaster GC7
  Samsung TV

-------------------------
Open
Settings
Diagnostics
Quit
```

If application count becomes too large, tray may only show managed/active applications.

---

# 27. Settings

Settings categories:

## General

    Start automatically
    Router enabled on startup
    Minimize to tray
    Close to tray
    stream polling interval

## Display Mappings

Example table:

    Samsung TV     -> Samsung HDMI
    Odyssey G5     -> Sound Blaster GC7
    ASUS VP278     -> Sound Blaster GC7

## Applications

Saved rules:

    Plezy      AUTO
    Brave      AUTO
    Discord    GC7
    Steam      IGNORE

## Audio Devices

Show:

    available
    unavailable saved devices
    sink name
    description
    associated card/profile

## Advanced

    custom matching rules
    resourceClass
    processBinary
    application.name
    media.name

---

# 28. Diagnostics

Diagnostics are a core product feature.

The UI should expose enough information to debug without terminal commands.

Sections:

    PipeWire / PulseAudio status
    Audio outputs
    Audio streams
    KWin windows
    Applications
    Display mappings
    Routing decisions
    Recent errors

Button:

    Copy Diagnostics

Possible output:

```text
Screen Audio Router Diagnostics

Router: enabled

APPLICATION
Plezy
resourceClass: com.edde746.plezy
window output: DP-3
audio match: processBinary=mpv
rule: AUTO
mapped destination: Samsung TV
active streams: 327
current sink: Samsung TV
status: OK
```

---

# 29. Logging

Use normal structured application logging.

Example:

```text
[16:22:03] INFO  Plezy window DP-2 -> DP-3
[16:22:03] INFO  Plezy rule AUTO
[16:22:03] INFO  display DP-3 -> Samsung TV audio
[16:22:03] INFO  matched audio stream 327 (mpv)
[16:22:03] INFO  moved stream 327 -> Samsung TV
```

Manual:

```text
[16:25:14] INFO  Plezy routing changed AUTO -> Sound Blaster GC7
[16:25:14] INFO  moved stream 327 -> Sound Blaster GC7
```

Errors:

```text
[16:30:02] WARN  Samsung TV audio unavailable
[16:30:02] INFO  no fallback selected; keeping current routing
```

---

# 30. Configuration

Configuration location:

    ~/.config/screen-audio-router/

Primary file:

    config.json

Use versioning.

Conceptual format:

```json
{
  "version": 1,
  "enabled": true,
  "settings": {
    "pollIntervalMs": 2000,
    "startMinimized": false,
    "closeToTray": true
  },
  "displayMappings": [],
  "applications": []
}
```

Do not write corrupted configuration directly over the only copy.

Recommended save process:

    write temporary file
    validate
    atomic replace

---

# 31. Example Application Rule

Plezy:

```json
{
  "id": "plezy",
  "name": "Plezy",
  "mode": "auto",
  "windowMatch": {
    "resourceClass": "com.edde746.plezy"
  },
  "audioMatch": {
    "processBinary": "mpv"
  },
  "fixedOutput": null
}
```

Discord example:

```json
{
  "id": "discord",
  "name": "Discord",
  "mode": "fixed",
  "windowMatch": {
    "resourceClass": "discord"
  },
  "audioMatch": {
    "processBinary": "Discord"
  },
  "fixedOutput": "audio-gc7"
}
```

Do not assume exact Discord metadata without runtime discovery.

---

# 32. Device Availability

Devices may disappear.

Examples:

    TV powered off
    HDMI disconnect
    USB GC7 disconnected
    PipeWire restart

Required behavior:

- application stays running
- saved configuration remains
- missing device is marked unavailable
- do not choose random fallback
- retry resolution when devices reappear

Possible UI:

    Plezy
    AUTO
    Destination: Samsung TV [Unavailable]

---

# 33. PipeWire Restart Handling

If PipeWire restarts:

    old stream IDs become invalid

Router must:

1. refresh outputs
2. refresh active streams
3. rebuild stream associations
4. reapply managed application rules where appropriate

Persistent configuration must never depend on stream IDs.

---

# 34. Performance

Normal idle CPU use should be negligible.

If V1 polls:

    1-2 second interval is acceptable

Avoid:

    tight while loops
    repeated shell commands dozens of times per second
    repeated routing of already-correct streams

Cache parsed state where useful.

---

# 35. Security

Application runs entirely as the logged-in user.

No root daemon.

No sudo required during normal use.

No system-wide PipeWire changes by default.

Autostart should use:

    user systemd service

or KDE user autostart.

---

# 36. Installation

Development mode should run directly from the repository.

Example:

    python -m screen_audio_router

User installation may later install:

    Python package / executable
    KWin script
    user systemd service
    desktop file
    icon

Provide:

    scripts/install-user.sh
    scripts/uninstall-user.sh

Installation must be reversible.

---

# 37. Old Plezy Router Migration

Do not disable the current working Plezy router until the new router proves itself.

Migration phase:

1. detect existing Plezy router files
2. display warning/information
3. test new router with Plezy
4. confirm successful AUTO routing
5. offer migration
6. disable old timer/script
7. preserve files as fallback unless user chooses removal

Do not automatically delete old files.

---

# 38. Development Phases

## Phase 1 - Project Foundation

Implement:

- project structure
- PySide6 dependency setup
- README
- DESIGN.md
- logging foundation
- basic config models

No KWin changes yet.

---

## Phase 2 - Audio Backend

Implement:

- list outputs
- list active streams
- parse pactl metadata
- current output detection
- move stream
- optional card profile activation

Provide CLI/debug command.

Tests required.

Do not proceed until the existing Plezy stream can be moved TV <-> GC7 through the new backend.

---

## Phase 3 - KWin Integration

Implement:

- KWin window discovery
- resourceClass
- resourceName
- caption
- PID
- output
- window added
- window removed
- window activated
- window output changed

Create DBus communication between KWin script and application.

No complex routing yet.

---

## Phase 4 - Application Registry

Combine:

    windows
    audio streams
    processes

into unified application objects.

Show debug table:

    application
    window class
    PID
    display
    streams
    current output

Prove Plezy -> mpv association.

---

## Phase 5 - Router Engine

Implement modes:

    IGNORE
    AUTO
    FIXED_DEVICE

Implement display mapping resolution.

Integration test Plezy.

---

## Phase 6 - Configuration Persistence

Save:

    application rules
    display mappings
    settings

Restore correctly after restart.

---

## Phase 7 - Basic GUI

Implement live application/process list.

Required:

    application name
    screen
    audio output
    routing selector
    active audio status

---

## Phase 8 - Manual Routing

Selecting any audio device from the application row:

    immediately move streams
    save FIXED_DEVICE
    AUTO stops

Selecting AUTO:

    save AUTO
    immediately evaluate current display
    move audio accordingly

This is a critical acceptance phase.

Implemented Phase 8 behavior: only a direct GUI routing-mode/device selection or
`Apply Now` invokes the manual routing controller. The controller persists rules,
evaluates `RouterEngine`, and calls `RouteExecutor` only for `MOVE`. Execution is
off the Qt event loop and the live backend is refreshed afterward. KWin events and
audio polling update decisions but do not execute them; continuous AUTO behavior
remains outside Phase 8.

---

## Phase 9 - Automatic Discovery UX

Unknown apps:

    appear automatically
    default IGNORE

No manual Add Application requirement.

Advanced configuration may still exist.

Implemented Phase 9 behavior: live window-only and audio-only registry entries are
annotated in the GUI view model as configured only when an explicit persisted
application rule exists. Otherwise they remain unconfigured, show a subtle `NEW`
indicator, and resolve to `IGNORE` without writing configuration or executing
routing. Explicit Ignore, Auto, or fixed-device selections persist a rule and clear
the indicator; Phase 8 execution remains limited to those direct user actions.
`Reset Configuration` removes only the selected application rule, returns the live
entry to unconfigured Ignore, and never executes audio routing. New unconfigured
applications receive one non-modal status-bar notice per GUI session.

---

## Phase 10 - System Tray

Implement:

    background operation
    tray
    quick routing
    router enable/disable

Implemented Phase 10 behavior: a dedicated `DesktopApplication` owns the single
main window, native Qt `SystemTrayController`, runtime services, and idempotent
shutdown. When a tray is available, closing hides the existing window while KWin
and audio discovery continue; Open restores that same instance. The tray provides
Open, synchronized/persisted Router Enabled, logical application count, and Quit.
Quit and SIGINT stop workers and timers, release DBus ownership, and remove the tray
icon. Without a tray, closing quits safely. Hidden/runtime events continue to update
decisions only and never execute routing; continuous AUTO remains outside Phase 10.

---

## Phase 11 - Diagnostics

Implement:

    live diagnostics
    copy diagnostics
    routing history
    errors

Implemented Phase 11 behavior: a typed read-only diagnostic snapshot is built from
the existing runtime registry, KWin state, audio outputs/streams, persisted config,
and centrally produced `RouteDecision` objects. A reusable tabbed diagnostics dialog
shows health overview, applications and routing explanations, output/stream matching,
KWin windows, display mappings, configuration counts, and a timezone-aware bounded
session event history (`maxlen=200`). Explicit routing results and runtime/config/DBus
errors feed the history. Copy Diagnostics produces a sanitized plain-text clipboard
report with URL query data and common secret fields redacted. Diagnostics starts no
poller, claims no DBus service, writes no configuration, and executes no routing.

---

## Phase 12 - Autostart

Implement user-session startup.

Application/router survives GUI closing where intended.

Implemented Phase 12 behavior: typed `AutostartManager` state is derived from one
managed freedesktop user entry under `$XDG_CONFIG_HOME/autostart/` (or
`~/.config/autostart/`). Enable uses an atomic same-directory write and validates a
stable managed marker and direct `screen-audio-router --autostart` Exec; disable is
idempotent and removes only that managed entry. Unrelated target content is rejected.
The main-window checkbox reflects filesystem state and reports failures through the
existing UI and diagnostic event history without changing router-enabled state.
Autostart launches runtime quietly in the tray, while manual or no-tray launches show
the window. Startup never executes routing. No systemd service or root access is used.

---

## Phase 13 - Continuous AUTO Runtime Routing

The earlier migration phase is obsolete because the standalone Plezy automation
was intentionally removed before this phase and must not be recreated.

Implemented Phase 13 behavior: a dedicated `AutomaticRoutingController` consumes
reconciled registry/audio snapshots, waits for initial KWin and audio readiness,
and performs one controlled startup reconciliation. It then coalesces relevant
changes per configured AUTO application with a 250 ms debounce, asks
`RouterEngine` for fresh policy, and executes only `MOVE` decisions off the Qt
thread. Generations invalidate queued work after application/config/global changes;
mode, controlling display, destination availability, and matched stream membership
are revalidated immediately before execution. A shared execution lock serializes
automatic and manual backend moves.

Unknown/unconfigured, IGNORE, FIXED_DEVICE, unmapped, missing-output, ambiguous,
and unmatched states never execute automatically. Successful movement relies on
the next audio snapshot becoming `ALREADY_CORRECT`, preventing loops; failures wait
for a meaningful later event rather than retrying aggressively. Automatic results
are recorded in the existing bounded diagnostic history, remain active while tray
hidden or autostarted, obey Router Enabled, and shut down before runtime workers.
No display-mapping UI, default-sink change, card-profile guessing, or Phase 14
feature is included.

Phase 14C implementation status: the Settings window now presents connected
physical displays and retained disconnected mappings with stable audio-output
choices and explicit unavailable states. Validated EDID identities are resolved
from the current immutable discovery snapshot, so a stable mapping survives a
connector change; legacy connector mappings remain compatible and lose priority
to an explicit stable mapping.

Assignment, removal, and explicit confirmed legacy migration delegate to the
typed `DisplayMappingService`. No startup or Settings-open migration occurs.
Successful persisted changes request reevaluation only for configured AUTO
applications on the affected current connector through the existing automatic
routing controller. Diagnostics report discovery health, physical identity,
connector, mapping source, availability, migration state, and mapping-operation
events without exposing raw EDID.

Phase 14D live acceptance validated Plezy/mpv routing across Odyssey G5 and ASUS
VP278 on GC7 and Samsung TV on its exact HDMI output. It also added conservative
post-move backend revalidation, deferred audio refresh handling, shutdown safety,
and focused main-window usability fixes without changing global sink state.

---

# Phase 14 — Per-Display Audio Output Mapping

## Purpose

The user must be able to configure which audio output an application should
use when its controlling window is located on a specific display.

The core model is:

    DISPLAY -> AUDIO OUTPUT

Example:

    Samsung Odyssey G5 -> Sound Blaster GC7
    ASUS VP278         -> Sound Blaster GC7
    Samsung TV         -> Samsung TV HDMI

Applications using AUTO mode follow these mappings.

The router must NOT assume that every display requires a different audio
output.


## Core Behavior

For an application in AUTO mode:

1. Determine the application's controlling window.
2. Determine which physical display contains that window.
3. Find the configured audio output for that display.
4. Compare the configured output with the application's current audio output.
5. Move the application's audio only if the outputs differ.

Conceptually:

    controlling window
           |
           v
        display
           |
           v
    display mapping
           |
           v
      audio output


## Example

Configuration:

    DISPLAY                 AUDIO OUTPUT
    ----------------------------------------------
    Samsung Odyssey G5      Sound Blaster GC7
    ASUS VP278              Sound Blaster GC7
    Samsung TV              Samsung TV HDMI

Plezy in AUTO mode:

    Plezy on Odyssey
        -> GC7

    Plezy on ASUS
        -> GC7

    Plezy on Samsung TV
        -> Samsung TV HDMI


## Important Multi-Monitor Behavior

Moving an application between two displays that map to the same audio
output must NOT cause unnecessary audio movement.

Example:

    Odyssey -> ASUS

Mappings:

    Odyssey -> GC7
    ASUS    -> GC7

Result:

    NO AUDIO MOVE

The desired output did not change.


Moving:

    ASUS -> Samsung TV

Mappings:

    ASUS       -> GC7
    Samsung TV -> Samsung HDMI

Result:

    route application audio to Samsung HDMI.


Moving:

    Samsung TV -> Odyssey

Mappings:

    Samsung TV -> Samsung HDMI
    Odyssey    -> GC7

Result:

    route application audio to GC7.


## Unmapped Displays

The user must be allowed to leave a display without an audio mapping.

Example:

    Odyssey -> GC7
    ASUS    -> No automatic routing
    TV      -> Samsung HDMI

If an AUTO application moves onto ASUS:

    NO AUDIO ACTION

The router must not guess an output.

An unmapped display means:

    "Do not automatically change audio while the application is on this
    display."


## GUI

Settings must provide a simple display mapping interface.

Example:

    DISPLAY AUDIO MAPPINGS

    Samsung Odyssey G5
    Audio Output:
    [ Sound Blaster GC7 ▼ ]

    ASUS VP278
    Audio Output:
    [ Sound Blaster GC7 ▼ ]

    Samsung TV
    Audio Output:
    [ Samsung TV HDMI ▼ ]

Available choices must include:

    No automatic routing
    --------------------
    Sound Blaster GC7
    Samsung TV HDMI
    <other dynamically discovered outputs>


The displays and audio outputs must be dynamically discovered.

Do not hardcode the current machine's device names.


## Application Routing Modes

Per-display mappings only apply to applications in AUTO mode.

Routing priority:

### IGNORE

The router never changes this application's audio.

Display mappings have no effect.


### FIXED_DEVICE

The application always uses the explicitly selected audio output.

Example:

    Plezy:
        FIXED_DEVICE -> Sound Blaster GC7

Even if Plezy moves to Samsung TV:

    audio remains on GC7

Display mappings MUST NOT override FIXED_DEVICE.


### AUTO

The application's controlling window determines which display mapping is
used.

Example:

    Plezy:
        mode = AUTO

    Samsung TV mapping:
        Samsung HDMI

Result:

    Plezy on Samsung TV -> Samsung HDMI


## Switching Back to AUTO

If the user changes an application from FIXED_DEVICE to AUTO:

1. Find the application's controlling window.
2. Find its current display.
3. Resolve that display's audio mapping.
4. If a mapping exists, immediately apply it.
5. If no mapping exists, do not move audio.

Example:

Plezy is physically on Samsung TV.

Current mode:

    FIXED_DEVICE -> GC7

User selects:

    AUTO

TV mapping:

    Samsung TV -> Samsung HDMI

Result:

    immediately route Plezy to Samsung HDMI.


## Avoid Redundant Routing

Before moving any stream, RouterEngine must compare:

    current audio output

against:

    desired mapped output

If they are already equal:

    NO ACTION

Do not repeatedly call move_stream() when the stream is already routed
correctly.


## Display Identity

Persistent mappings must identify physical displays as reliably as
possible.

Do not persist only:

    DP-3
    DP-2
    HDMI-A-1

because connector assignments may change.

Where available use:

- manufacturer
- model
- serial
- EDID-derived identity
- connector as runtime/fallback metadata
- other useful KScreen/KWin identity

Example:

    Samsung TV
        persistent identity: <stable display identity>
        current connector: DP-3
        mapped audio: Samsung HDMI


## Audio Output Identity

Do not persist dynamic numeric sink IDs.

Use stable AudioBackend output identity.

Example current-machine outputs:

    Sound Blaster GC7
    Samsung HDMI

Their current sink names may be used where appropriate by AudioBackend,
but configuration must use the project's stable audio-output identity
model.


## Missing Display

If a configured physical display is disconnected:

- retain its mapping
- mark the display unavailable
- do not delete the mapping
- do not transfer its mapping to another display

When the display returns and can be safely identified, restore the mapping.


## Missing Audio Output

If a display's mapped audio device is unavailable:

Example:

    Samsung TV -> Samsung HDMI

but Samsung HDMI currently does not exist.

Result:

    DEVICE_MISSING

Do not choose another output automatically.

Keep the configured mapping and retry when the device becomes available.


## Configuration

Extend the versioned configuration schema.

Conceptual example:

```json
{
  "display_audio_mappings": {
    "odyssey-display-id": {
      "audio_output": "gc7-output-id"
    },
    "asus-display-id": {
      "audio_output": "gc7-output-id"
    },
    "samsung-tv-display-id": {
      "audio_output": "samsung-hdmi-output-id"
    }
  }
}

# 39. Critical Acceptance Tests

## Test A - Plezy AUTO

1. Plezy on Odyssey G5.
2. Playback active.
3. Rule AUTO.
4. Audio -> GC7.
5. Move Plezy to Samsung TV.
6. Audio -> TV.
7. Move back.
8. Audio -> GC7.

PASS requires all transitions without manually refreshing UI.

---

## Test B - Manual override

1. Plezy on Samsung TV.
2. Rule AUTO.
3. Audio -> TV.
4. Select GC7 from routing dropdown.
5. Audio -> GC7.
6. Wait 10 seconds.
7. Audio remains GC7.
8. Move Plezy to another monitor.
9. Audio remains GC7.
10. Select AUTO.
11. Audio immediately routes according to current display.

Any automatic move during FIXED_DEVICE mode is a failure.

---

## Test C - New stream

1. Plezy on Samsung TV.
2. Rule AUTO.
3. No playback / no mpv stream.
4. Start playback.
5. New stream appears.
6. Router detects it.
7. Audio routes to Samsung TV automatically.

---

## Test D - Unknown application

1. Launch an application never seen before.
2. It appears in list.
3. Rule is IGNORE.
4. Router does not move its audio.

---

## Test E - Device disappears

1. Plezy AUTO -> Samsung TV.
2. Disconnect/power off TV audio device.
3. Application does not crash.
4. UI shows destination unavailable.
5. No arbitrary output is selected.
6. Reconnect device.
7. Router discovers it again.
8. AUTO routing recovers.

---

## Test F - Router disabled

1. Disable global Router Enabled toggle.
2. Move managed application across screens.
3. Audio must not be moved.
4. Re-enable.
5. Rules resume.

---

# 40. Codex Working Rules

Codex must follow these rules while working on this project.

1. Read DESIGN.md before making architectural changes.

2. Do not rewrite the project architecture without explaining why.

3. Work one development phase at a time.

4. Run tests before claiming a phase is finished.

5. Do not disable the existing Plezy router before Phase 13.

6. Do not use X11-only window tools.

7. Do not hardcode current DP-3 or GC7 values as universal constants.

8. Do not persist numeric PipeWire/Pulse IDs.

9. Do not put `pactl` subprocess logic inside GUI widgets.

10. Do not silently reroute unknown applications.

11. Manual FIXED_DEVICE always overrides AUTO.

12. Missing devices must not trigger arbitrary fallback routing.

13. Prefer small understandable modules over a single large script.

14. Update README / DESIGN notes when implementation behavior changes.

15. After each phase, report:
    - files created/modified
    - tests performed
    - test results
    - known limitations
    - next recommended phase

---

# 41. Codex Start Command

A useful initial instruction for Codex:

```text
Read DESIGN.md completely.

Treat it as the source of truth for the Screen Audio Router project.

Do not modify or disable the existing working Plezy automation.

Begin with Phase 1 only.

Inspect the repository first, then create the project structure required by
DESIGN.md.

Use Python 3 + PySide6 unless you discover a concrete technical blocker that
makes C++/Qt6 materially better. If you choose differently, document the
reason before implementation.

After Phase 1:
- run all available checks
- summarize files created
- describe the architecture
- stop and wait for the next instruction

Do not begin Phase 2 until instructed.
```

---

# 42. Long-term Ideas

Not required for V1:

- native PipeWire backend
- per-window browser routing
- profiles such as Gaming / TV / Headphones
- hotkeys
- KDE Plasma widget
- automatic Bluetooth headset profiles
- stream volume rules
- per-display preferred volume
- multi-seat support
- import/export configuration
- rule priority editor
- media application detection
- temporary overrides with timeout
- per-game Steam rules
- KDE notifications for route changes

These must not delay the core V1.

---

# 43. V1 Definition of Done

V1 is complete when:

- application runs normally on CachyOS KDE Wayland
- running applications are automatically listed
- active audio applications are visible
- displays are discovered
- audio devices are discovered
- display-to-audio mappings can be configured
- each application can be set to IGNORE, AUTO, or a fixed audio device
- AUTO follows window display
- fixed device mode never gets overridden by AUTO
- new streams are detected
- rules persist after reboot
- router can run in background/system tray
- diagnostics exist
- Plezy works at least as reliably as the existing shell/KWin prototype
- old Plezy automation can be safely disabled after migration

Until all of those are true, the existing Plezy setup remains the fallback.

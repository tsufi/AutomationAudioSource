# Screen Audio Router

Screen Audio Router is a KDE Plasma 6/Wayland desktop application that will route
individual application audio streams according to the display containing the
application's controlling window. It is designed for PipeWire through its
PulseAudio compatibility layer and never needs to change the global default sink
for normal routing.

The project is currently at **Phase 10: System Tray and Desktop Lifecycle**. It can inspect the local
PipeWire/PulseAudio-compatible server and perform an explicitly requested stream
move through a reusable backend. A separate KWin 6 script reports live Wayland
window facts over user-session DBus. An in-process registry groups windows by
stable application identity and conservatively associates audio streams. It does
not perform continuous automatic routing. The native Qt window shows live application,
screen, audio-output, routing-mode, and decision status data. It persists `IGNORE`,
`AUTO`, `FIXED_DEVICE`, fixed-output, and global enabled selections without
executing those decisions automatically. A direct mode/device selection or
`Apply Now` action can explicitly apply the selected application's current decision.
Newly discovered window-only and audio-only applications appear automatically with
a subtle `NEW` marker, but remain unconfigured and resolve to the safe `IGNORE`
mode. Discovery, startup, polling, and runtime events neither persist a rule nor
move audio. A rule becomes persistent only after the user explicitly selects
Ignore, Auto, or a fixed device.

## Architecture

The package keeps the boundaries defined in [DESIGN.md](DESIGN.md):

- `audio`: replaceable audio backend boundary and audio-domain models
- `kwin`: Wayland-native KWin event integration boundary
- `router`: application registry and routing policy boundary
- `config`: versioned models, validation, and atomic persistence
- `gui`: thin Qt presentation and asynchronous runtime orchestration; it does not
  parse `pactl` or own routing policy
- `router.manual_routing`: the single controller for user-requested persistence,
  decision evaluation, and per-stream execution
- `logging`: centralized application logging configuration

The removed Plezy-specific standalone automation is not recreated by this project.

## Target platform

- CachyOS / Arch Linux
- KDE Plasma 6, KWin, and Wayland
- PipeWire with PulseAudio compatibility
- Python 3.11 or newer and Qt 6 via PySide6

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Run from the repository after installation:

```bash
python -m screen_audio_router
```

The GUI owns the project DBus receiver while it is running, obtains KWin events
live, and discovers audio streams and outputs on a worker thread so the Qt event
loop remains responsive. Only one Screen Audio Router GUI or debug watcher can own
the receiver service at a time. Close the other instance if startup reports a DBus
service conflict.

The application table is sorted with active applications first, then windowed and
audio-only entries. Selecting a row shows its stable ID, controlling screen,
current audio, decision, runtime object counts, and fixed-output choices. Missing
saved fixed outputs remain visible as unavailable. Phase 7 statuses such as
`Would route to …` are pending decisions. Selecting `AUTO`, selecting a fixed
device, or pressing `Apply Now` is an explicit user action and may move only the
selected application's confidently matched streams. KWin events, application
activation, stream polling, stream appearance, startup, and the global enabled
toggle never execute routing in Phase 8.

Manual operations run on a dedicated worker and refresh the real audio backend
afterward. Routine results appear inline and in the status bar, including partial
failure if a stream disappears during a multi-stream move. No rollback or fallback
device is attempted, and the global default sink is never changed.

The details panel distinguishes configured rules from the safe unconfigured
default. `Reset Configuration` removes only the selected application's persisted
rule, leaves matcher aliases and display mappings intact, keeps the runtime row
visible while the application is running, and does not move audio. The application
then returns to unconfigured `IGNORE`. A lightweight status-bar message reports a
new unconfigured application only once per GUI session; no application-history
database is maintained.

## System tray and lifecycle

On desktops with a system tray, Screen Audio Router shows a native Qt tray icon
with `Open`, a checkable `Router Enabled` action, the logical application count,
and `Quit`. Closing the main window hides it while keeping the same runtime,
KWin DBus ownership, application registry, and audio discovery worker alive.
`Open` restores the same window and state; it does not construct another runtime.
The first close in a session shows a brief tray reminder.

The tray and main-window enabled controls share the same configuration state.
Changing either control persists the existing global setting and refreshes routing
decisions, but does not move audio. A hidden tray runtime likewise does not enable
continuous automatic routing: audio moves remain limited to explicit Phase 8 user
actions.

`Quit` and terminal Ctrl+C use the same clean shutdown path, releasing the KWin
DBus service, stopping polling and timers, waiting for worker executors, and removing
the tray icon. If no system tray is available, closing the main window quits instead
of leaving an invisible process. Login autostart is not implemented yet. Independently
launched instances may still conflict over the single KWin DBus receiver service.

The configuration is created on first run at
`~/.config/screen-audio-router/config.json`. Override the base configuration
directory for development or tests with `XDG_CONFIG_HOME`.

Run checks:

```bash
pytest
ruff check .
```

## Audio debug CLI

Read-only discovery commands:

```bash
python -m screen_audio_router audio status
python -m screen_audio_router audio outputs
python -m screen_audio_router audio streams
python -m screen_audio_router audio cards
```

The following commands mutate audio state and therefore require explicit stable
card/output names and a dynamic stream ID copied from the discovery output:

```bash
python -m screen_audio_router audio move <STREAM_ID> <SINK_NAME>
python -m screen_audio_router audio set-profile <CARD_NAME> <PROFILE_NAME>
```

`move` validates both objects before invoking `pactl move-sink-input` and verifies
the resulting stream destination. It never changes the default/global output.

## KWin development integration

Install or update the new project-specific script as the current user:

```bash
scripts/install-kwin-script.sh
```

This installs package ID `screen-audio-router`, enables it in `kwinrc`, and asks
KWin to reconfigure. It does not touch the existing `plezy-audio-router` package.
If KWin does not pick up an updated package, the debug commands below explicitly
reload only `screen-audio-router`; a logout/login should normally not be needed.

```bash
python -m screen_audio_router kwin status
python -m screen_audio_router kwin windows
python -m screen_audio_router kwin windows --all
python -m screen_audio_router kwin watch
python -m screen_audio_router kwin watch --all
```

`windows` starts the user-session DBus receiver before reloading the reporting
script, guaranteeing an initial snapshot of already-open windows. `watch` keeps
the receiver running and prints additions, removals, activations, metadata
updates, and output changes. Every window remains independently identified by
its session-only KWin UUID. No KWin event is connected to audio movement.

## Application registry debug CLI

```bash
python -m screen_audio_router apps list
python -m screen_audio_router apps watch
python -m screen_audio_router apps explain com.edde746.plezy
```

The registry preserves window-only and audio-only applications, exposes unmatched
audio, and selects one deterministic controlling window without routing anything.
Matching is explainable: exact PID, normalized identity, or an explicit alias.
The bootstrap alias catalog contains the integration-proven Plezy window identity
to `mpv` relationship. Multiple indistinguishable alias streams remain unmatched.

## Routing decision debug CLI

Routing intent is temporary in Phase 5 and must be supplied explicitly. For example:

```bash
python -m screen_audio_router router list
python -m screen_audio_router router explain com.edde746.plezy --mode ignore
python -m screen_audio_router router explain com.edde746.plezy --mode fixed \
  --fixed-output <SINK_NAME>
python -m screen_audio_router router explain com.edde746.plezy --mode auto \
  --map '<DISPLAY>=<SINK_NAME>'
```

An explicit apply command is available for manual integration testing only:

```bash
python -m screen_audio_router router apply <APP> --mode fixed \
  --fixed-output <SINK_NAME>
```

`router apply` executes only the listed `MOVE` streams. No KWin event, registry
refresh, or polling callback invokes the executor automatically. Display mappings
and rules are read from persistent configuration by default. Explicit `--mode`,
`--fixed-output`, and `--map` arguments override persisted values for that command.

## Persistent configuration

The versioned configuration lives at
`$XDG_CONFIG_HOME/screen-audio-router/config.json`, falling back to
`~/.config/screen-audio-router/config.json`. Writes use a temporary file in the
same directory, `fsync`, atomic replacement, and directory `fsync`.

```bash
python -m screen_audio_router config path
python -m screen_audio_router config show
python -m screen_audio_router config validate
python -m screen_audio_router config apps
python -m screen_audio_router config displays
python -m screen_audio_router config set-enabled true
python -m screen_audio_router config set-app <APP> ignore
python -m screen_audio_router config set-app <APP> auto
python -m screen_audio_router config set-app <APP> fixed <SINK_NAME>
python -m screen_audio_router config map-display <DISPLAY_ID> <SINK_NAME>
python -m screen_audio_router config unmap-display <DISPLAY_ID>
```

Current audio identities are stable pactl sink names. Until richer KScreen/EDID
discovery is implemented, mappings created by the CLI explicitly mark their
display identity as `connector_fallback`; connector names are not claimed to be
physically stable. Missing displays and outputs remain in configuration.

Malformed JSON and unsupported future schema versions are rejected without
overwriting the file. An invalid fixed-device application rule is skipped with a
visible warning so other valid rules remain usable. Unknown top-level fields are
preserved across a load/save round trip; unknown nested fields are currently not
preserved. Unknown applications always resolve to `IGNORE`.

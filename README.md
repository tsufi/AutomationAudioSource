# Screen Audio Router

Screen Audio Router is a KDE Plasma 6/Wayland desktop application that will route
individual application audio streams according to the display containing the
application's controlling window. It is designed for PipeWire through its
PulseAudio compatibility layer and never needs to change the global default sink
for normal routing.

Version **1.0.0** is ready for normal user-level installation. A KWin 6 script
reports live Wayland window facts over user-session DBus, while an in-process
registry groups windows by stable application identity and conservatively
associates audio streams. The application continuously routes explicitly configured
`AUTO` applications after initial runtime readiness. The native Qt window shows live application,
screen, audio-output, routing-mode, and decision status data. It persists `IGNORE`,
`AUTO`, `FIXED_DEVICE`, fixed-output, and global enabled selections. Runtime
execution applies only to configured AUTO rules. A direct mode/device selection or
`Apply Now` action can explicitly apply the selected application's current decision.
Newly discovered window-only and audio-only applications appear automatically with
a subtle `NEW` marker, but remain unconfigured and resolve to the safe `IGNORE`
mode. Discovery never persists a rule or promotes an unknown application to AUTO.
A rule becomes persistent only after the user explicitly selects
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
- `router.automatic_routing`: the dedicated debounced coordinator for fresh,
  configured AUTO decisions and serialized background execution
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

## Install and uninstall

Install for the current user without root access:

```bash
./scripts/install.sh
```

This creates a dedicated environment under the user XDG data directory, installs
the executable in `~/.local/bin`, installs the launcher and icon, and installs the
KWin reporter script. Re-running the command updates the same installation. Ensure
`~/.local/bin` is on the login `PATH` before enabling autostart.

Uninstall application-owned files while preserving all routing rules and mappings:

```bash
./scripts/uninstall.sh
```

Configuration remains at `~/.config/screen-audio-router/`. Remove that directory
separately only when a deliberate full configuration reset is wanted.

The GUI owns the project DBus receiver while it is running, obtains KWin events
live, and discovers audio streams and outputs on a worker thread so the Qt event
loop remains responsive. Only one Screen Audio Router GUI or debug watcher can own
the receiver service at a time. Close the other instance if startup reports a DBus
service conflict.

The application table is sorted with active applications first, then windowed and
audio-only entries. Selecting a row shows its stable ID, controlling screen,
current audio, decision, runtime object counts, and fixed-output choices. Missing
saved fixed outputs remain visible as unavailable. Statuses such as
`Would route to …` explain current policy. Selecting `AUTO`, selecting a fixed
device, or pressing `Apply Now` is an explicit user action and may move only the
selected application's confidently matched streams. After runtime readiness,
KWin changes, stream polling, stream appearance, and output changes also reevaluate
configured AUTO applications without changing IGNORE or FIXED_DEVICE rules.

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
with shortcuts for the main window, Display Mappings, Diagnostics, and About, plus
a checkable `Router Enabled` action, the logical application count, and `Quit`.
Closing the main window hides it while keeping the same runtime,
KWin DBus ownership, application registry, and audio discovery worker alive.
`Open` restores the same window and state; it does not construct another runtime.
The first close in a session shows a brief tray reminder.

The tray and main-window enabled controls share the same configuration state.
Disabling invalidates queued AUTO work; enabling alone does not sweep applications.
A later relevant runtime event resumes configured AUTO routing. A hidden tray
runtime uses exactly the same continuous policy as a visible window.

`Quit` and terminal Ctrl+C use the same clean shutdown path, releasing the KWin
DBus service, stopping polling and timers, waiting for worker executors, and removing
the tray icon. If no system tray is available, closing the main window quits instead
of leaving an invisible process. The GUI holds a user-session lock, so a second
launch exits cleanly before creating
another routing runtime. Read-only command-line inspection remains independent.

## Diagnostics

Open `Diagnostics` from the main window for a read-only, live view built from the
existing runtime snapshot. The Overview shows router, backend, KWin, DBus, and
configuration health plus logical application, output, stream, and window counts.
Additional tabs explain applications and `RouteDecision` results, audio outputs,
stream matching confidence and reasons, unmatched/ambiguous streams, KWin windows,
read-only display mappings, configuration counts, and recent session events.

`Refresh` re-renders existing registry/runtime state; it does not create another
audio poller or claim another DBus service. `Copy Diagnostics` puts a plain-text
support report on the clipboard. URLs have query values removed and common secret,
token, authorization, API-key, and password fields are redacted. The report does
not dump environment variables or raw configuration JSON.

Diagnostics never changes configuration, matching, or routing. It contains no
route/apply/reset actions and never invokes `RouteExecutor`. Recent events are kept
only in memory for the current session, with a maximum of 200 entries.

## Start automatically at login

The main window provides `Start automatically at login`. Its state comes directly
from the managed XDG entry at
`$XDG_CONFIG_HOME/autostart/screen-audio-router.desktop`, falling back to
`~/.config/autostart/`. No redundant setting is stored in `config.json`, no root
access is needed, and no systemd service is installed.

Enabling writes a complete `.desktop` file atomically with the marker
`X-ScreenAudioRouter-Managed=true` and the direct command
`screen-audio-router --autostart`. The package console entrypoint must be available
on the login PATH; otherwise the UI reports an error rather than creating a broken
entry. An unrelated file at the managed path is never overwritten or removed.

`--autostart` starts runtime and the tray quietly without raising the main window.
If a tray is unavailable, runtime startup fails, or configuration cannot be loaded
safely, the GUI/error remains visible. Manual launches continue to show the main
window. Autostart and Router Enabled are independent. After initial KWin and audio
state are ready, either launch mode performs one conservative reconciliation of
configured AUTO applications and then uses the same continuous policy.

## Continuous AUTO routing

`AUTO` follows existing persisted display-to-output mappings while Screen Audio
Router runs. A controlling-window display change, a confidently matched stream
appearing, or a mapped destination reappearing can trigger reevaluation. Unmapped
displays leave audio untouched, and missing outputs never fall back to another
device. Unknown applications remain unconfigured `IGNORE`; runtime events do not
execute `FIXED_DEVICE` rules.

The conservative matcher remains a hard safety boundary. Ambiguous or unmatched
streams—including indistinguishable standalone `mpv` streams—are never moved.
Per-application events are coalesced for 250 ms, stale work is revalidated before
execution, and backend moves are serialized with manual routing off the Qt event
loop. Results and failures appear in diagnostics without modal dialogs. Automatic
routing never changes the default sink or guesses a sound-card profile.

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

Current audio identities are stable pactl sink names. The GUI discovers validated
EDID identities for physical displays. CLI connector mappings remain explicit
legacy fallbacks and can be converted safely in Display Mappings. Missing displays
and outputs remain in configuration without arbitrary reassignment.

Malformed JSON and unsupported future schema versions are rejected without
overwriting the file. An invalid fixed-device application rule is skipped with a
visible warning so other valid rules remain usable. Unknown top-level fields are
preserved across a load/save round trip; unknown nested fields are currently not
preserved. Unknown applications always resolve to `IGNORE`.

## Physical display mappings

The **Display Mappings** window discovers connected DRM displays and identifies
them using validated EDID serial or fingerprint identities when possible. Choose
an audio output for each display, or choose **No automatic routing**. Persisted
disconnected displays and unavailable mapped outputs remain visible and are never
silently reassigned.

Older connector mappings remain compatible. When one can be matched safely to a
single physical display, Settings offers an explicit, confirmed conversion; the
application never migrates mappings merely because it started or Settings was
opened. Successful mapping changes ask the existing AUTO controller to reevaluate
only AUTO applications currently on the affected connector. IGNORE and
FIXED_DEVICE rules remain unaffected.

Phase 14D live acceptance verified Plezy routing between GC7 and the Samsung HDMI
output using stable physical display mappings. Opening or refreshing Settings is
read-only unless the user explicitly changes or converts a mapping.

## Application icon

The official icon is packaged at `src/screen_audio_router/assets/icon.png`, so
repository and installed Python launches do not depend on the current directory.
Run `scripts/install-desktop-assets.sh` to install the 1254×1254 icon and launcher
under the current user's XDG data directory. The launcher, KDE desktop identity,
and managed autostart entry use the icon name `screen-audio-router`.

## Troubleshooting and limitations

- `pactl` must be available and able to reach the user's PipeWire/PulseAudio server.
- KDE Plasma 6, KWin, and Wayland are the supported window-tracking environment.
- If display discovery or an audio output is unavailable, routing remains a safe
  no-action and Diagnostics reports the failure.
- Ambiguous audio streams are intentionally left untouched.
- No global default sink is changed, and no root access is required.

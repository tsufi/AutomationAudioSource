#!/usr/bin/env bash
set -euo pipefail

data_dir="${XDG_DATA_HOME:-$HOME/.local/share}"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}"
install_dir="$data_dir/screen-audio-router"
executable="$HOME/.local/bin/screen-audio-router"
desktop_file="$data_dir/applications/screen-audio-router.desktop"
icon_file="$data_dir/icons/hicolor/1254x1254/apps/screen-audio-router.png"
autostart_file="$config_dir/autostart/screen-audio-router.desktop"
kwin_package="$data_dir/kwin/scripts/screen-audio-router"

if [[ -L "$executable" && "$(readlink -- "$executable")" == "$install_dir/venv/bin/screen-audio-router" ]]; then
  rm -- "$executable"
fi
rm -f -- "$desktop_file" "$icon_file"
if [[ -f "$autostart_file" ]] && grep -qx 'X-ScreenAudioRouter-Managed=true' "$autostart_file"; then
  rm -- "$autostart_file"
fi
rm -rf -- "$install_dir"
if command -v kpackagetool6 >/dev/null 2>&1; then
  kpackagetool6 --type=KWin/Script --remove screen-audio-router >/dev/null 2>&1 || true
else
  rm -rf -- "$kwin_package"
fi

printf 'Uninstalled Screen Audio Router. Configuration was preserved at %s/screen-audio-router/.\n' "$config_dir"

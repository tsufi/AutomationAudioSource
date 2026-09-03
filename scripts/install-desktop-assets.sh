#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname -- "$script_dir")"
data_dir="${XDG_DATA_HOME:-$HOME/.local/share}"
icon_dir="$data_dir/icons/hicolor/1254x1254/apps"
application_dir="$data_dir/applications"

install -Dm644 "$project_dir/src/screen_audio_router/assets/icon.png" \
  "$icon_dir/screen-audio-router.png"
install -Dm644 "$project_dir/resources/screen-audio-router.desktop" \
  "$application_dir/screen-audio-router.desktop"

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$data_dir/icons/hicolor" >/dev/null 2>&1 || true
fi

printf 'Installed Screen Audio Router desktop assets under %s\n' "$data_dir"

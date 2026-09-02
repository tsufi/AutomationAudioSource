#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
package_dir="$project_dir/kwin/screen-audio-router"
package_id="screen-audio-router"

if kpackagetool6 --type=KWin/Script --show "$package_id" >/dev/null 2>&1; then
    kpackagetool6 --type=KWin/Script --upgrade "$package_dir"
else
    kpackagetool6 --type=KWin/Script --install "$package_dir"
fi

kwriteconfig6 --file kwinrc --group Plugins --key "${package_id}Enabled" true
qdbus6 org.kde.KWin /KWin org.kde.KWin.reconfigure

echo "Installed and enabled KWin script: $package_id"
echo "Use 'python -m screen_audio_router kwin windows' to reload it and obtain a snapshot."

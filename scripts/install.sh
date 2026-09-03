#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname -- "$script_dir")"
data_dir="${XDG_DATA_HOME:-$HOME/.local/share}"
bin_dir="${HOME}/.local/bin"
install_dir="$data_dir/screen-audio-router"
venv_dir="$install_dir/venv"
python_command="${PYTHON:-python3}"

if [[ -e "$bin_dir/screen-audio-router" || -L "$bin_dir/screen-audio-router" ]]; then
  if [[ ! -L "$bin_dir/screen-audio-router" ]] || \
    [[ "$(readlink -- "$bin_dir/screen-audio-router")" != "$venv_dir/bin/screen-audio-router" ]]; then
    printf 'Refusing to replace unrelated executable: %s\n' "$bin_dir/screen-audio-router" >&2
    exit 1
  fi
fi

"$python_command" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || {
  printf 'Screen Audio Router requires Python 3.11 or newer.\n' >&2
  exit 1
}

mkdir -p "$install_dir" "$bin_dir"
if [[ ! -x "$venv_dir/bin/python" ]]; then
  "$python_command" -m venv "$venv_dir"
fi
"$venv_dir/bin/python" -m pip install --upgrade "$project_dir"
ln -sfn "$venv_dir/bin/screen-audio-router" "$bin_dir/screen-audio-router"
"$script_dir/install-desktop-assets.sh"
"$script_dir/install-kwin-script.sh"

printf 'Installed Screen Audio Router. Ensure %s is on PATH.\n' "$bin_dir"

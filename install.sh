#!/bin/sh
set -eu

raw_root="${HARNESS_LINK_RAW_ROOT:-https://raw.githubusercontent.com/sileod/harness-link/main}"
repo="${HARNESS_LINK_RAW_BASE:-$raw_root/bin}"
install_dir="${HARNESS_LINK_INSTALL_DIR:-${HARNESS_INSTALL_DIR:-${ALBERT_INSTALL_DIR:-$HOME/.local/bin}}}"

command -v python3 >/dev/null 2>&1 || {
  echo "harness-link: python3 is required" >&2
  exit 1
}
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' || {
  echo "harness-link: Python 3.9 or newer is required" >&2
  exit 1
}

mkdir -p "$install_dir/harness_link"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM

for name in harness-link hlink harness-link-spawn albert albert-spawn nim nim-spawn inferx inferx-spawn orfree orfree-spawn; do
  curl -fsSL "$repo/$name" -o "$tmp_dir/$name"
  chmod +x "$tmp_dir/$name"
  mv "$tmp_dir/$name" "$install_dir/$name"
done

for module in __init__.py credentials.py providers.py cli.py hlink.py spawn.py; do
  curl -fsSL "$raw_root/src/harness_link/$module" -o "$tmp_dir/$module"
  mv "$tmp_dir/$module" "$install_dir/harness_link/$module"
done

trap - EXIT HUP INT TERM

echo "Installed Harness Link (hlink, albert, nim, inferx, orfree) to $install_dir"
case ":${PATH:-}:" in
  *":$install_dir:"*) ;;
  *) echo "Add $install_dir to PATH" ;;
esac

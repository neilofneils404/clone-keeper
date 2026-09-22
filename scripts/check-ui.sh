#!/usr/bin/env bash
# Use Qt 6: Arch's unqualified qmllint may be the older Qt 5 executable.
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
shell_dir="${OMARCHY_PATH:-/usr/share/omarchy}/shell"

if [[ -n ${QMLLINT:-} ]]; then
  lint=$QMLLINT
elif [[ -x /usr/lib/qt6/bin/qmllint ]]; then
  lint=/usr/lib/qt6/bin/qmllint
elif command -v qmllint6 >/dev/null 2>&1; then
  lint=$(command -v qmllint6)
else
  lint=$(command -v qmllint || true)
fi

if [[ -z $lint ]] || [[ $("$lint" --version 2>&1) != *"qmllint 6."* ]]; then
  echo "Clone Keeper requires Qt 6 qmllint. Set QMLLINT to its executable path." >&2
  exit 1
fi
if [[ ! -f $shell_dir/Ui/qmldir || ! -f $shell_dir/Commons/qmldir ]]; then
  echo "Omarchy shell modules were not found in $shell_dir." >&2
  exit 1
fi

# Quickshell normally maps qs.* to its configuration root. Reproduce that
# mapping for the standalone linter without changing the installed shell.
import_dir=$(mktemp -d "${TMPDIR:-/tmp}/clone-keeper-qmllint.XXXXXX")
trap 'rm -rf -- "$import_dir"' EXIT
ln -s -- "$shell_dir" "$import_dir/qs"

"$lint" --version
"$lint" --ignore-settings -I "$import_dir" \
  "$project_dir/BarWidget.qml" "$project_dir/Panel.qml"
# No warning suppression or exit-code masking: Qt 6 returns failure on errors.
# It also prints type warnings for the host's dynamic QObject APIs.
omarchy-plugin-validate "$project_dir"

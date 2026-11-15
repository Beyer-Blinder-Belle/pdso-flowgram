#!/bin/sh
# Copy the PDSO extension code to the Revit computer and load the new code.
#
# Usage: PDSO_SSH_HOST=user@host PDSO_REMOTE_EXT=C:/path/PDSO.extension tools/deploy_vm.sh
#
# PDSO_REMOTE_EXT is the extension folder on the Revit computer: the clone of
# this repository (named *.extension, for example PDSO.extension).
# Environment:
#   PDSO_SSH_HOST   SSH target of the Revit computer (user@host). Necessary.
#   PDSO_REMOTE_EXT Extension folder (the clone) on the Revit computer. Necessary.
#   PDSO_RELOAD     How to load the new code. Default: auto.
#                   dev     POST /pdso/dev/reload (fast: drops the cached pdso
#                           modules). It needs the dev flag file
#                           %APPDATA%\pyRevit\pdso_api_dev.flag.
#                   pyrevit Reload pyRevit (POST /pyrevit-core/sessions/). It
#                           needs no flag file. It takes about 30 seconds and
#                           reloads all pyRevit extensions.
#                   auto    dev, and pyrevit if the dev routes are off.
#                   none    Copy only.
set -eu

HOST="${PDSO_SSH_HOST:?Set PDSO_SSH_HOST to the SSH target of the Revit computer}"
REMOTE="${PDSO_REMOTE_EXT:?Set PDSO_REMOTE_EXT to the extension folder there}"
MODE="${PDSO_RELOAD:-auto}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="http://127.0.0.1:48884"

remote() { ssh "$HOST" "$1" </dev/null; }

# Copy lib/ and startup.py. Revit keeps some image files of lib/common/assets
# open, so the copy of those files can fail ("Can't unlink", "Permission
# denied"). That is not an error: the check below makes sure that the code is
# there.
REMOTE_WIN="$(printf '%s' "$REMOTE" | tr '/' '\\')"
COPYFILE_DISABLE=1 tar -C "$ROOT" --exclude='__pycache__' --exclude='*.pyc' -czf - lib startup.py \
  | ssh "$HOST" "tar -xzf - -C \"$REMOTE_WIN\"" 2>/dev/null || true

CHECK="lib/pdso/code/api/handlers.py"
LOCAL_SUM="$(shasum -a 256 "$ROOT/$CHECK" | cut -d' ' -f1)"
REMOTE_SUM="$(remote "type \"$REMOTE_WIN\\$(printf '%s' "$CHECK" | tr '/' '\\')\"" | shasum -a 256 | cut -d' ' -f1)"
if [ "$LOCAL_SUM" != "$REMOTE_SUM" ]; then
  echo "copy failed: $CHECK on the Revit computer is not the same as the local file" >&2
  exit 1
fi
echo "copied"

reload_dev() {
  REPLY="$(remote "curl.exe -s -m 60 --noproxy * -X POST $BASE/pdso/dev/reload" || true)"
  case "$REPLY" in
    *'"ok": true'*) echo "reloaded the pdso modules (dev route)"; return 0 ;;
    *) DEV_REPLY="$REPLY"; return 1 ;;
  esac
}

reload_pyrevit() {
  remote "curl.exe -s -m 300 --noproxy * -X POST $BASE/pyrevit-core/sessions/ -H \"Content-Type: application/json\" -d {}" >/dev/null || true
  # The PDSO routes are gone for some seconds while pyRevit reloads.
  TRIES=0
  while [ "$TRIES" -lt 60 ]; do
    REPLY="$(remote "curl.exe -s -m 10 --noproxy * $BASE/pdso/status" || true)"
    case "$REPLY" in
      *'"ok": true'*) echo "reloaded pyRevit"; return 0 ;;
    esac
    TRIES=$((TRIES + 1))
    sleep 3
  done
  echo "pyRevit reload: the PDSO API did not answer: $REPLY" >&2
  return 1
}

case "$MODE" in
  none) ;;
  dev) reload_dev || { echo "reload failed: $DEV_REPLY" >&2; exit 1; } ;;
  pyrevit) reload_pyrevit ;;
  auto) reload_dev || reload_pyrevit ;;
  *) echo "PDSO_RELOAD must be auto, dev, pyrevit or none" >&2; exit 1 ;;
esac

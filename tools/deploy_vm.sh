#!/bin/sh
# Copy the PDSO extension code to the Revit computer and reload the API modules.
#
# Usage: PDSO_SSH_HOST=user@host PDSO_REMOTE_EXT=C:/path/PDSO.extension tools/deploy_vm.sh
# Environment (both are necessary):
#   PDSO_SSH_HOST   SSH target of the Revit computer (user@host)
#   PDSO_REMOTE_EXT PDSO.extension folder on the Revit computer
#
# The reload needs the dev flag file %APPDATA%\pyRevit\pdso_api_dev.flag on the
# Revit computer (see POST /pdso/dev/reload).
set -eu

HOST="${PDSO_SSH_HOST:?Set PDSO_SSH_HOST to the SSH target of the Revit computer}"
REMOTE="${PDSO_REMOTE_EXT:?Set PDSO_REMOTE_EXT to the PDSO.extension folder there}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

find "$ROOT/PDSO.extension" -name '__pycache__' -prune -exec rm -rf {} +
scp -q -r "$ROOT/PDSO.extension/lib" "$ROOT/PDSO.extension/startup.py" "$HOST:$REMOTE/" </dev/null

REPLY="$(ssh "$HOST" 'curl -s -m 60 -X POST http://127.0.0.1:48884/pdso/dev/reload' </dev/null)"
case "$REPLY" in
  *'"ok": true'*) echo "deployed and reloaded" ;;
  *) echo "reload failed: $REPLY" >&2; exit 1 ;;
esac

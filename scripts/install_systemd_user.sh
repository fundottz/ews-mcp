#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_NAME="exchange-mail-sync.service"
TIMER_NAME="exchange-mail-sync.timer"
DISABLE_LEGACY_CRON=0
START_NOW=1

usage() {
  cat <<'EOF'
Usage: install_systemd_user.sh [--disable-legacy-cron] [--no-start]

Install exchange mail sync as a systemd user service + timer.

Options:
  --disable-legacy-cron  Remove old crontab entries that call sync_exchange_mail.sh
  --no-start             Enable timer but do not trigger the service immediately
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --disable-legacy-cron)
      DISABLE_LEGACY_CRON=1
      shift
      ;;
    --no-start)
      START_NOW=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

mkdir -p "$UNIT_DIR"

python3 - "$REPO_ROOT" "$UNIT_DIR/$SERVICE_NAME" "$REPO_ROOT/assets/systemd/exchange-mail-sync.service.in" <<'PY'
from pathlib import Path
import sys
repo_root, dest, template = sys.argv[1:4]
content = Path(template).read_text(encoding='utf-8').replace('@REPO_ROOT@', repo_root)
Path(dest).write_text(content, encoding='utf-8')
PY

python3 - "$UNIT_DIR/$TIMER_NAME" "$REPO_ROOT/assets/systemd/exchange-mail-sync.timer.in" <<'PY'
from pathlib import Path
import sys
_, dest, template = sys.argv
Path(dest).write_text(Path(template).read_text(encoding='utf-8'), encoding='utf-8')
PY

systemctl --user daemon-reload
systemctl --user enable --now "$TIMER_NAME"

if [[ "$START_NOW" -eq 1 ]]; then
  systemctl --user start "$SERVICE_NAME"
fi

if [[ "$DISABLE_LEGACY_CRON" -eq 1 ]]; then
  tmp_before="$(mktemp)"
  tmp_after="$(mktemp)"
  if crontab -l >"$tmp_before" 2>/dev/null; then
    grep -v 'sync_exchange_mail\.sh' "$tmp_before" >"$tmp_after" || true
    if ! cmp -s "$tmp_before" "$tmp_after"; then
      crontab "$tmp_after"
    fi
  fi
  rm -f "$tmp_before" "$tmp_after"
fi

echo "Installed $SERVICE_NAME and $TIMER_NAME in $UNIT_DIR"
systemctl --user --no-pager --full status "$TIMER_NAME" || true

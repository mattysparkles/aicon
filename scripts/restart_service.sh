#!/usr/bin/env bash
set -euo pipefail

# Safe restart helper for AICon
# Attempts, in order:
# 1) systemd service 'aicon'
# 2) HUP existing gunicorn via .app.pid
# 3) Start a new daemonized gunicorn bound to PORT (default 5000)

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

PORT="${PORT:-5000}"
PIDFILE=".app.pid"
LOGFILE="aicon.log"
GUNICORN_BIN="${GUNICORN_BIN:-$APP_DIR/venv/bin/gunicorn}"

echo "[restart] Attempting systemctl restart aicon ..."
if command -v systemctl >/dev/null 2>&1; then
  if systemctl status aicon >/dev/null 2>&1; then
    if sudo systemctl restart aicon; then
      echo "[restart] systemd service restarted."
      exit 0
    fi
  fi
fi

restart_gunicorn() {
  echo "[restart] Starting gunicorn daemon on port ${PORT} ..."
  if [ ! -x "$GUNICORN_BIN" ]; then
    echo "[restart] gunicorn not found at $GUNICORN_BIN"
    exit 1
  fi
  # Remove stale pidfile
  if [ -f "$PIDFILE" ] && ! kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
    rm -f "$PIDFILE"
  fi
  nohup "$GUNICORN_BIN" --bind 0.0.0.0:"$PORT" app:app \
    --daemon --pid "$PIDFILE" --log-file "$LOGFILE" >/dev/null 2>&1 || {
      echo "[restart] Failed to launch gunicorn"; exit 1; }
  echo "[restart] gunicorn started. PID: $(cat "$PIDFILE" 2>/dev/null || echo '?'), log: $LOGFILE"
}

if [ -f "$PIDFILE" ]; then
  PID="$(cat "$PIDFILE" || true)"
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    echo "[restart] Sending HUP to PID $PID ..."
    if kill -HUP "$PID" 2>/dev/null; then
      sleep 1
      if kill -0 "$PID" 2>/dev/null; then
        echo "[restart] Process still running after HUP (ok)."
        exit 0
      fi
    fi
    echo "[restart] HUP failed or process exited; attempting TERM ..."
    kill -TERM "$PID" 2>/dev/null || true
    sleep 1
  fi
fi

restart_gunicorn

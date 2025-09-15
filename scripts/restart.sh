#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "[+] Stopping any existing app.py..."
pkill -f "python.*app.py" || true
sleep 1

PY=venv/bin/python
if [ ! -x "$PY" ]; then
  echo "[!] venv/bin/python not found; falling back to system python"
  PY=python
fi

echo "[+] Using Python: $($PY -V 2>/dev/null || echo 'not found')"

# Ensure dependencies are up to date
if [ -x "venv/bin/pip" ]; then
  echo "[+] Installing/upgrading dependencies..."
  venv/bin/pip install -r requirements.txt || true
fi

echo "[+] Starting app..."
nohup $PY app.py > aicon.log 2>&1 &
PID=$!
echo "$PID" > .app.pid
sleep 2

echo "[+] Running processes:"
ps aux | grep -E "(venv/bin/python|python).*app.py" | grep -v grep || true

echo "[+] Last 80 log lines:"
tail -n 80 aicon.log || true

echo "[+] Done."

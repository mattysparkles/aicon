#!/usr/bin/env bash
set -euo pipefail

THRESHOLD=${THRESHOLD:-90}
USAGE=$(df -h / | awk 'NR==2 {gsub("%","",$5); print $5}')

if [ "$USAGE" -ge "$THRESHOLD" ]; then
  echo "Disk usage ${USAGE}% exceeds threshold ${THRESHOLD}%" > /tmp/disk_alert.txt
  if [ -n "${SENDGRID_API_KEY:-}" ] && [ -n "${SENDGRID_FROM_EMAIL:-}" ] && [ -n "${ADMIN_EMAILS:-}" ]; then
    python - <<'PY'
import os
from handlers.email import send_email
emails = os.environ.get('ADMIN_EMAILS','').split(',')
for e in emails:
    e=e.strip()
    if e:
        send_email(e, 'AICon Disk Usage Alert', 'Server disk usage exceeded threshold.')
PY
  fi
fi

#!/usr/bin/env bash
set -euo pipefail

LOG_FILE="aicon.log"
MAX_SIZE_MB=${MAX_SIZE_MB:-50}
KEEP=${KEEP:-5}

if [ ! -f "$LOG_FILE" ]; then
  exit 0
fi

size_mb=$(du -m "$LOG_FILE" | awk '{print $1}')
if [ "$size_mb" -lt "$MAX_SIZE_MB" ]; then
  exit 0
fi

ts=$(date +%Y%m%d_%H%M%S)
mv "$LOG_FILE" "${LOG_FILE%.log}_$ts.log"
: > "$LOG_FILE"

ls -1t ${LOG_FILE%.log}_*.log 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f

#!/bin/bash

APP_DIR="/opt/apps/aicon"
APP_FILE="$APP_DIR/app.py"
PORT="5050"
DOMAIN="aicon.sparkleserver.site"

# 1. Change Flask port to 5050
echo "[+] Updating Flask app to use port $PORT..."
sed -i "s/app.run(\(.*\))/app.run(host='0.0.0.0', port=$PORT)/" "$APP_FILE"

# 2. Add/replace Caddyfile block
echo "[+] Updating Caddyfile..."
CADDYFILE="/etc/caddy/Caddyfile"

# Remove any existing block
sed -i "/$DOMAIN/,/}/d" "$CADDYFILE"

# Append new block
cat <<EOF >> "$CADDYFILE"
$DOMAIN {
    reverse_proxy localhost:$PORT
}
EOF

# 3. Reload Caddy
echo "[+] Reloading Caddy..."
systemctl reload caddy

# 4. Activate venv and run the app
echo "[+] Running app.py from $APP_DIR..."
cd "$APP_DIR"
source venv/bin/activate
pip install -r requirements.txt
nohup python app.py > aicon.log 2>&1 &
echo "[+] Deployed at https://$DOMAIN"
echo "[i] Set Twilio Voice and Messaging webhooks to: https://$DOMAIN/twilio (unified handler)"

#!/usr/bin/env bash

set -e

# Default configuration
REPO_URL=${REPO_URL:-"https://github.com/youruser/aicon.git"}
APP_DIR=${APP_DIR:-"/opt/aicon"}
PYTHON_VERSION=3.10
VENV_DIR="$APP_DIR/venv"

# 1. Install Python 3.10 and pip if not already present
if ! command -v python${PYTHON_VERSION} >/dev/null 2>&1 || ! command -v pip3 >/dev/null 2>&1; then
    echo "Installing Python ${PYTHON_VERSION} and pip..."
    sudo apt update
    sudo apt install -y python${PYTHON_VERSION} python${PYTHON_VERSION}-venv python3-pip
fi

# 2. Clone the repo if it doesn't exist
if [ ! -d "$APP_DIR" ]; then
    echo "Cloning repository from $REPO_URL into $APP_DIR..."
    git clone "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"

# 3. Create a virtual environment
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment at $VENV_DIR..."
    python${PYTHON_VERSION} -m venv "$VENV_DIR"
fi

# 4. Install requirements from requirements.txt
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
pip install -r requirements.txt

# 5. Install Gunicorn and run the app on port 5000
if ! command -v gunicorn >/dev/null 2>&1; then
    pip install gunicorn
fi

if [ "$1" != "--systemd" ]; then
    echo "Starting Gunicorn on port 5000..."
    nohup gunicorn --bind 0.0.0.0:5000 app:app >/tmp/aicon.log 2>&1 &
    echo "Gunicorn started. Logs: /tmp/aicon.log"
fi

# 6. Optionally set up a systemd service for auto-restart
if [ "$1" = "--systemd" ]; then
    SERVICE_FILE=/etc/systemd/system/aicon.service
    echo "Setting up systemd service at $SERVICE_FILE..."
    sudo tee "$SERVICE_FILE" >/dev/null <<SERVICE
[Unit]
Description=Gunicorn instance to serve aicon
After=network.target

[Service]
User=$USER
Group=www-data
WorkingDirectory=$APP_DIR
Environment="PATH=$VENV_DIR/bin"
ExecStart=$VENV_DIR/bin/gunicorn --bind 0.0.0.0:5000 app:app

[Install]
WantedBy=multi-user.target
SERVICE

    sudo systemctl daemon-reload
    sudo systemctl enable --now aicon
    echo "Systemd service 'aicon' installed and started."
fi

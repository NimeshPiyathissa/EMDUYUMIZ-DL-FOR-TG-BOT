#!/bin/bash
set -e

echo "[1/5] Installing system packages..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv ffmpeg screen curl wget unzip

echo "[2/5] Installing Bento4 SDK..."
mkdir -p tools /opt/bento4
cd tools
if [ ! -f "Bento4-SDK-1-6-0-641.x86_64-unknown-linux.zip" ]; then
    wget -q https://www.bok.net/Bento4/binaries/Bento4-SDK-1-6-0-641.x86_64-unknown-linux.zip
fi
unzip -qo Bento4-SDK-1-6-0-641.x86_64-unknown-linux.zip
sudo cp -f Bento4-SDK-1-6-0-641.x86_64-unknown-linux/bin/* /usr/local/bin/
sudo cp -rf Bento4-SDK-1-6-0-641.x86_64-unknown-linux/* /opt/bento4/
sudo chmod +x /usr/local/bin/mp4*
cd ..

echo "[3/5] Setting up Python virtual environment..."
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo "[4/5] Preparing configuration files..."
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp .env.example .ent
fi
if [ ! -f "auth_users.json" ] && [ -f "auth_users.json.example" ]; then
    cp auth_users.json.example auth_users.json
fi

echo "[5/5] Creating systemd background service..."
PROJECT_DIR=$(pwd)
sudo bash -c "cat << SERVICE > /etc/systemd/system/edumix.service
[Unit]
Description=Edumix Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/venv/bin/python3 $PROJECT_DIR/edumix_v4.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE"

sudo systemctl daemon-reload
sudo systemctl enable edumix
echo "Done! Run: nano .env && nano auth_users.json && systemctl start edumix"


#!/bin/bash
set -e

# Ensure script is run with bash
if [ -z "$BASH_VERSION" ]; then
    echo "ERROR: Please run this script with bash: bash install.sh"
    exit 1
fi

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$PROJECT_DIR"

echo "=========================================================="
echo "🎬 Edumix Telegram Bot — Automated VPS Setup"
echo "=========================================================="

# 1. Install system packages
echo ""
echo "[1/6] Installing required system packages..."
sudo apt-get update -y
sudo apt-get install -y wget unzip curl git python3 python3-pip python3-venv ffmpeg screen

# 2. Install Bento4 SDK
echo ""
echo "[2/6] Installing Bento4 SDK..."
sudo mkdir -p /opt/bento4 /usr/local/bin tools
BENTO4_ZIP="Bento4-SDK-1-6-0-641.x86_64-unknown-linux.zip"
if [ ! -f "tools/$BENTO4_ZIP" ]; then
    echo "Downloading Bento4 SDK..."
    wget -q --show-progress -O "tools/$BENTO4_ZIP" "https://www.bok.net/Bento4/binaries/$BENTO4_ZIP"
fi
unzip -qo "tools/$BENTO4_ZIP" -d tools/
sudo cp -rf tools/Bento4-SDK-1-6-0-641.x86_64-unknown-linux/bin/* /opt/bento4/
sudo chmod +x /opt/bento4/*

# Create symlinks in /usr/local/bin for Bento4 tools
for tool in mp4decrypt mp4dump mp4info mp4encrypt mp4fragment; do
    if [ -f "/opt/bento4/$tool" ]; then
        sudo ln -sf "/opt/bento4/$tool" "/usr/local/bin/$tool"
    fi
done

# 3. Setup Python virtual environment
echo ""
echo "[3/6] Setting up Python virtual environment..."
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
sudo ln -sf "$PROJECT_DIR/venv/bin/yt-dlp" /usr/local/bin/yt-dlp

# 4. Configuration files setup (.env & auth_users.json)
echo ""
echo "[4/6] Configuring environment & credentials..."
if [ ! -f "auth_users.json" ]; then
    if [ -f "auth_users.json.example" ]; then
        cp auth_users.json.example auth_users.json
    else
        echo "[]" > auth_users.json
    fi
fi
chmod 644 auth_users.json

# Check if .env already exists and has API_ID configured
ENV_CONFIGURED=false
if [ -f ".env" ]; then
    if grep -q "API_ID=" .env && [ -n "$(grep 'API_ID=' .env | cut -d= -f2- | tr -d '[:space:]')" ]; then
        ENV_CONFIGURED=true
    fi
fi

if [ "$ENV_CONFIGURED" = true ]; then
    echo "Existing .env configuration detected. Preserving credentials."
else
    if [ ! -f ".env" ] && [ -f ".env.example" ]; then
        cp .env.example .env
    fi

    # Interactive prompt if a terminal is attached
    if [ -t 0 ]; then
        echo ""
        echo "Configure Telegram Bot Credentials (press Enter to skip and edit manually later):"
        read -r -p "Enter Telegram API_ID: " INPUT_API_ID
        read -r -p "Enter Telegram API_HASH: " INPUT_API_HASH
        read -r -p "Enter Telegram BOT_TOKEN: " INPUT_BOT_TOKEN
        read -r -p "Enter Admin Telegram User ID (ADMIN_ID): " INPUT_ADMIN_ID

        if [ -n "$INPUT_API_ID" ]; then
            sed -i "s/^API_ID=.*/API_ID=$INPUT_API_ID/" .env
        fi
        if [ -n "$INPUT_API_HASH" ]; then
            sed -i "s/^API_HASH=.*/API_HASH=$INPUT_API_HASH/" .env
        fi
        if [ -n "$INPUT_BOT_TOKEN" ]; then
            sed -i "s/^BOT_TOKEN=.*/BOT_TOKEN=$INPUT_BOT_TOKEN/" .env
        fi
        if [ -n "$INPUT_ADMIN_ID" ]; then
            sed -i "s/^ADMIN_ID=.*/ADMIN_ID=$INPUT_ADMIN_ID/" .env
        fi
    else
        echo "Non-interactive shell detected; template copied to .env. Edit with 'nano .env'."
    fi
fi
chmod 600 .env

# 5. Create and register systemd background service
echo ""
echo "[5/6] Registering systemd background service..."
sudo bash -c "cat << SERVICE > /etc/systemd/system/edumix.service
[Unit]
Description=Edumix Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
Environment=\"PATH=$PROJECT_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin\"
ExecStart=$PROJECT_DIR/venv/bin/python3 $PROJECT_DIR/edumix_v4.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE"

sudo systemctl daemon-reload
sudo systemctl enable edumix

# 6. Post-install verification check
echo ""
echo "[6/6] Running post-install verification checks..."
ALL_PASS=true
for cmd in ffmpeg ffprobe yt-dlp mp4decrypt; do
    if which $cmd >/dev/null 2>&1; then
        echo "  [OK] Found tool: $(which $cmd)"
    else
        echo "  [FAIL] Missing tool: $cmd"
        ALL_PASS=false
    fi
done

if ./venv/bin/python3 -c "import pyrogram, tgcrypto, dotenv, yt_dlp; print('  [OK] Python dependencies verified successfully')" 2>/dev/null; then
    :
else
    echo "  [FAIL] Python dependency import failed."
    ALL_PASS=false
fi

echo ""
echo "=========================================================="
if [ "$ALL_PASS" = true ]; then
    echo "🎉 Installation completed successfully!"
    echo "To start the bot, run:"
    echo "  sudo systemctl start edumix"
    echo "To view live logs, run:"
    echo "  sudo journalctl -u edumix -f"
else
    echo "⚠️ Installation completed with warnings. Please check the logs above."
fi
echo "=========================================================="

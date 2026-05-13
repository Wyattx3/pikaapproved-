#!/bin/sh
set -e

# Restore Telegram session from base64 env var
if [ -n "$SESSION_B64" ]; then
    echo "$SESSION_B64" | base64 -d > /app/pika_session.session
    echo "[*] Session file restored from SESSION_B64"
else
    echo "[!] SESSION_B64 not set — session file missing"
    exit 1
fi

# Copy image if provided via env (optional)
if [ -n "$IMAGE_B64" ]; then
    mkdir -p /root/Downloads
    echo "$IMAGE_B64" | base64 -d > "/root/Downloads/Untitled design.png"
    echo "[*] Image file restored from IMAGE_B64"
fi

exec python bot.py

#!/bin/sh
set -e

if [ -z "$SESSION_STRING" ]; then
    echo "[!] SESSION_STRING env var not set"
    exit 1
fi

# Optional: restore image
if [ -n "$IMAGE_B64" ]; then
    mkdir -p /root/Downloads
    echo "$IMAGE_B64" | base64 -d > "/root/Downloads/Untitled design.png"
    echo "[*] Image restored"
fi

exec python bot.py

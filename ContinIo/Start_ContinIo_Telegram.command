#!/bin/zsh
set -e
cd -- "$(dirname -- "$0")"
if [ ! -f continio_telegram.env ]; then
  echo "Run Setup_ContinIo_Telegram.command first."
  read "reply?Press Enter to close..."; exit 1
fi
source continio_telegram.env
if [ -z "${CONTINIO_DASHBOARD_URL:-}" ] || [[ "$CONTINIO_DASHBOARD_URL" == http://127.0.0.1:* ]]; then
  mac_ip=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
  if [ -n "$mac_ip" ]; then
    export CONTINIO_DASHBOARD_URL="http://${mac_ip}:1880/dashboard/teacher"
  fi
fi
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "Installing the free tunnel tool..."
  if command -v brew >/dev/null 2>&1; then brew install cloudflared; else echo "Install Homebrew or cloudflared first."; exit 1; fi
fi
if [ ! -x face_env/bin/python ]; then python3 -m venv face_env; fi
if [ ! -f face_env/.continio_final_ready ]; then
  face_env/bin/python -m pip install --upgrade pip
  face_env/bin/python -m pip install -r continio_face_requirements.txt
  touch face_env/.continio_final_ready
fi
face_env/bin/python download_face_models.py
log_file="/tmp/continio_cloudflared_$$.log"
cloudflared tunnel --url http://127.0.0.1:5055 > "$log_file" 2>&1 &
tunnel_pid=$!
cleanup(){ kill "$tunnel_pid" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
public_url=""
for attempt in {1..30}; do
  public_url=$(grep -Eo 'https://[-a-z0-9]+\.trycloudflare\.com' "$log_file" | head -1 || true)
  [ -n "$public_url" ] && break
  sleep 1
done
if [ -z "$public_url" ]; then echo "Tunnel failed:"; cat "$log_file"; exit 1; fi
export CONTINIO_PUBLIC_URL="$public_url"
echo "============================================================"
echo "ContinIo Final multi-room Telegram is starting"
echo "Secure review link: $public_url"
echo "A one-time /link command will appear below for each unlinked teacher."
echo "Send the correct teacher's command to your Telegram bot."
echo "Room 101 and Room 102 camera sources are configured."
echo "Both camera connections remain active; room usage follows the timetable."
echo "============================================================"
echo "Keep this window open."
exec face_env/bin/python continio_face_service.py

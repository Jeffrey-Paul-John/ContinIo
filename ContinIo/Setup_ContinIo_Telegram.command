#!/bin/zsh
set -e
cd -- "$(dirname -- "$0")"
echo "ContinIo Telegram setup"
echo "Create a bot with @BotFather, then paste its token here."
read -s "bot_token?Telegram bot token (hidden): "
echo
read "db_user?MySQL user [root]: "
db_user=${db_user:-root}
read -s "db_password?MySQL password (hidden): "
echo
echo "Enter either a local webcam index (0, 1...) or a full network-camera video URL."
echo "Examples: 0   or   http://192.168.1.51:8080/video"
read "camera_101?Room 101 camera source: "
read "camera_102?Room 102 camera source: "
echo "Enter the Teacher page URL reachable from the teacher's phone."
echo "Example: http://192.168.1.20:1880/dashboard/teacher"
read "dashboard_url?ContinIo Teacher page URL [http://127.0.0.1:1880/dashboard/teacher]: "
dashboard_url=${dashboard_url:-http://127.0.0.1:1880/dashboard/teacher}
{
  printf "export TELEGRAM_BOT_TOKEN=%q\n" "$bot_token"
  printf "export CONTINIO_DB_HOST=%q\n" "127.0.0.1"
  printf "export CONTINIO_DB_PORT=%q\n" "3306"
  printf "export CONTINIO_DB_USER=%q\n" "$db_user"
  printf "export CONTINIO_DB_PASSWORD=%q\n" "$db_password"
  printf "export CONTINIO_DB_NAME=%q\n" "continio"
  printf "export CONTINIO_CAMERA_101_URL=%q\n" "$camera_101"
  printf "export CONTINIO_CAMERA_102_URL=%q\n" "$camera_102"
  printf "export CONTINIO_DASHBOARD_URL=%q\n" "$dashboard_url"
} > continio_telegram.env
chmod 600 continio_telegram.env
echo "Configuration saved locally."
read "reply?Press Enter to close..."

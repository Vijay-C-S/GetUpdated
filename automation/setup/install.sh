#!/usr/bin/env bash
# One-time setup on the Oracle VM (Ubuntu or Oracle Linux, ARM or x86).
# Run from inside the cloned repo:   bash automation/setup/install.sh
set -euo pipefail

AUTO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_DIR="$(dirname "$AUTO_DIR")"
echo "Repo: $REPO_DIR"

echo "==> System packages"
if command -v apt-get >/dev/null; then
  sudo apt-get update -y
  sudo apt-get install -y git python3 python3-venv python3-pip fonts-dejavu-core curl cron
elif command -v dnf >/dev/null; then
  sudo dnf install -y git python3 python3-pip dejavu-sans-fonts curl cronie
  sudo systemctl enable --now crond
fi

echo "==> Python venv"
python3 -m venv "$AUTO_DIR/.venv"
"$AUTO_DIR/.venv/bin/pip" install -q --upgrade pip
"$AUTO_DIR/.venv/bin/pip" install -q -r "$AUTO_DIR/requirements.txt"

echo "==> Claude Code"
if ! command -v claude >/dev/null && [ ! -x "$HOME/.local/bin/claude" ]; then
  curl -fsSL https://claude.ai/install.sh | bash
fi
CLAUDE_BIN="$(command -v claude || echo "$HOME/.local/bin/claude")"
echo "Claude at: $CLAUDE_BIN"

echo "==> Config files"
[ -f "$AUTO_DIR/.env" ] || cp "$AUTO_DIR/.env.example" "$AUTO_DIR/.env"
if [ ! -f "$AUTO_DIR/config.json" ]; then
  CLAUDE_BIN="$CLAUDE_BIN" AUTO_DIR="$AUTO_DIR" python3 - <<'PY'
import json, os
d = os.environ["AUTO_DIR"]
cfg = json.load(open(f"{d}/config.example.json"))
cfg["claude_bin"] = os.environ["CLAUDE_BIN"]
json.dump(cfg, open(f"{d}/config.json", "w"), indent=2)
PY
fi
mkdir -p "$AUTO_DIR/state"

echo "==> Telegram bot service (systemd)"
sudo tee /etc/systemd/system/getupdated-bot.service >/dev/null <<EOF
[Unit]
Description=GetUpdated Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$AUTO_DIR
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$AUTO_DIR/.venv/bin/python $AUTO_DIR/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable getupdated-bot

echo "==> Cron: run the pipeline every 2 hours (8am-10pm IST)"
CRON_LINE="30 2-16/2 * * * cd $AUTO_DIR && PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin $AUTO_DIR/.venv/bin/python pipeline.py >> $AUTO_DIR/state/cron.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'GetUpdated\|pipeline.py' ; echo "# GetUpdated automation (times are UTC)"; echo "$CRON_LINE" ) | crontab -

cat <<'MSG'

Done. Remaining manual steps:
  1. Log in to Claude Code once:        claude      (then /login, then exit)
  2. Put your bot token in:            nano automation/.env
  3. Start the bot:                    sudo systemctl start getupdated-bot
  4. Message your bot on Telegram -> it replies with your chat id ->
     put TELEGRAM_CHAT_ID=<id> in automation/.env -> sudo systemctl restart getupdated-bot
  5. Test:   send /run to your bot   (or: automation/.venv/bin/python automation/pipeline.py)
MSG

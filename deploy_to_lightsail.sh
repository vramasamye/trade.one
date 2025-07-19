#!/usr/bin/env bash
# =============================================================================
# Deploy Groww Trading Bot to existing AWS Lightsail instance
# -----------------------------------------------------------------------------
# Usage:
#   ./deploy_to_lightsail.sh <LIGHTSAIL_IP> <SSH_USER> [PATH_TO_ENV_FILE]
#
# Example:
#   ./deploy_to_lightsail.sh 18.222.123.45 ubuntu .env
#
# Requirements:
#   • Existing Lightsail Ubuntu instance with SSH access
#   • .env file with bot configuration
# =============================================================================
set -euo pipefail

# --------------- Input validation ---------------
if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <LIGHTSAIL_IP> <SSH_USER> [PATH_TO_ENV_FILE]" >&2
  exit 1
fi

LIGHTSAIL_IP="$1"
SSH_USER="$2"
ENV_FILE_PATH="${3:-.env}"

if [[ ! -f "$ENV_FILE_PATH" ]]; then
  echo "[ERROR] Environment file '$ENV_FILE_PATH' not found." >&2
  exit 1
fi

# --------------- Constants ---------------
PROJECT_NAME="groww-trader"
ARCHIVE="/tmp/${PROJECT_NAME}.tar.gz"
REMOTE_DIR="/opt/${PROJECT_NAME}"
SERVICE_NAME="groww-trader"

# --------------- Package project ---------------
printf "📦 Packaging project...\n"
EXCLUDES=(--exclude .git --exclude "*/__pycache__" --exclude "*.swp" --exclude "*.pyc")
tar czf "$ARCHIVE" "${EXCLUDES[@]}" .

# --------------- Transfer files ---------------
printf "🚀 Deploying to %s...\n" "$LIGHTSAIL_IP"
scp -q -O "$ARCHIVE" "${SSH_USER}@${LIGHTSAIL_IP}:/tmp/"
scp -q -O "$ENV_FILE_PATH" "${SSH_USER}@${LIGHTSAIL_IP}:/tmp/${PROJECT_NAME}.env"

# --------------- Remote deployment ---------------
ssh "${SSH_USER}@${LIGHTSAIL_IP}" bash -s <<'EOF'
set -euo pipefail

PROJECT_NAME="groww-trader"
ARCHIVE="/tmp/${PROJECT_NAME}.tar.gz"
ENV_SOURCE="/tmp/${PROJECT_NAME}.env"
REMOTE_DIR="/opt/${PROJECT_NAME}"
SERVICE_NAME="groww-trader"

# --- Install minimal dependencies ---
sudo apt-get update -q
sudo apt-get install -y python3 python3-venv python3-pip

# --- Create service user if needed ---
if ! id "growwtrader" &>/dev/null; then
    sudo useradd --system --create-home growwtrader
fi

# --- Setup application directory ---
sudo mkdir -p "${REMOTE_DIR}"
sudo tar xzf "${ARCHIVE}" -C "${REMOTE_DIR}" --strip-components=1
sudo cp "${ENV_SOURCE}" "${REMOTE_DIR}/.env"
sudo chown -R growwtrader:growwtrader "${REMOTE_DIR}"
sudo chmod 600 "${REMOTE_DIR}/.env"

# --- Setup Python environment ---
sudo -u growwtrader python3 -m venv "${REMOTE_DIR}/venv"
sudo -u growwtrader "${REMOTE_DIR}/venv/bin/pip" install --upgrade pip -q
sudo -u growwtrader "${REMOTE_DIR}/venv/bin/pip" install -r "${REMOTE_DIR}/requirements.txt" -q

# --- Create systemd service ---
sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null <<EOL
[Unit]
Description=Groww Trading Bot
After=network.target

[Service]
Type=simple
User=growwtrader
WorkingDirectory=${REMOTE_DIR}
EnvironmentFile=${REMOTE_DIR}/.env
ExecStart=${REMOTE_DIR}/venv/bin/python optimized_groww_trader.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOL

# --- Start service ---
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

# --- Check status ---
sleep 2
if sudo systemctl is-active --quiet "${SERVICE_NAME}"; then
    printf "✅ Bot deployed and running successfully!\n"
else
    printf "⚠️  Deployment complete but service not running. Check: sudo journalctl -u %s\n" "${SERVICE_NAME}"
fi
EOF

# --------------- Cleanup ---------------
rm -f "$ARCHIVE"

printf "\n🎉 Deployment complete!\n"
printf "Monitor: ssh %s@%s 'sudo journalctl -u %s -f'\n" "$SSH_USER" "$LIGHTSAIL_IP" "$SERVICE_NAME"

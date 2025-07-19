#!/bin/bash
# =============================================================================
# AWS Lightsail Instance Setup Script for Groww Trading Bot
# Run this script directly on a fresh Ubuntu 20.04/22.04 Lightsail instance
# =============================================================================

set -euo pipefail

PROJECT_NAME="groww-trader"
REMOTE_DIR="/opt/${PROJECT_NAME}"
SERVICE_NAME="groww-trader"

printf "🚀 Setting up Groww Trading Bot on AWS Lightsail...\n\n"

# --- Update system ---
printf "📦 Updating system packages...\n"
apt update && apt upgrade -y

# --- Install dependencies ---
printf "🔧 Installing required packages...\n"
apt install -y \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    build-essential \
    git \
    htop \
    unzip \
    curl \
    wget

# --- Create service user ---
printf "👤 Creating service user...\n"
if ! id "growwtrader" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash growwtrader
fi

# --- Create application directory ---
printf "📁 Setting up application directory...\n"
mkdir -p "${REMOTE_DIR}"
chown growwtrader:growwtrader "${REMOTE_DIR}"

# --- Create log directory ---
printf "📝 Setting up logging...\n"
mkdir -p /var/log/groww-trader
chown growwtrader:growwtrader /var/log/groww-trader

# --- Setup log rotation ---
tee "/etc/logrotate.d/groww-trader" > /dev/null <<EOL
/var/log/groww-trader/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0644 growwtrader growwtrader
    postrotate
        systemctl reload groww-trader 2>/dev/null || true
    endscript
}
EOL

# --- Configure firewall ---
printf "🔒 Configuring firewall...\n"
ufw allow OpenSSH
ufw --force enable

# --- Install AWS CLI (optional, for future management) ---
printf "☁️  Installing AWS CLI...\n"
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
./aws/install
rm -rf aws awscliv2.zip

printf "\n✅ Lightsail instance setup complete!\n"
printf "\nNext steps:\n"
printf "1. Upload your project files to ${REMOTE_DIR}\n"
printf "2. Create .env file with your configuration\n"
printf "3. Install Python dependencies\n"
printf "4. Create and start the systemd service\n"
printf "\nOr use the deploy_to_lightsail.sh script from your local machine.\n"
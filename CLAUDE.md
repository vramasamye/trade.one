# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a NIFTY Options Trading Bot that implements an automated breakout strategy using the Groww API. The bot monitors first 15-minute candles, detects breakouts, waits for retests, and sends trading signals via Telegram. It's designed as a defensive security tool for educational purposes.

## Core Architecture

### Main Components

- **OptimizedGrowwTrader** (`optimized_groww_trader.py`): Main trading engine with real-time data processing
  - Manages GrowwAPI connection and live data feed
  - Implements breakout detection and retest confirmation logic
  - Handles strategy state management and signal generation
  - Contains market timing validation and safety checks

- **TelegramNotifier** (`telegram_notifier.py`): Notification system with rate limiting
  - Rate-limited message queue system
  - Specialized notification functions for different events
  - Daily scheduler for morning messages
  - Async message handling with python-telegram-bot

### Strategy Implementation

The bot follows a 4-phase strategy:
1. **First 15-min candle capture** (9:15-9:30 AM)
2. **5-minute breakout monitoring** (9:30 AM onwards)
3. **1-minute retest confirmation** (after breakout)
4. **Signal generation** (CE for bullish, PE for bearish)

### Data Flow

1. Live NIFTY data via GrowwFeed WebSocket connection
2. Real-time tick processing with candle aggregation
3. Strategy state updates based on time and price conditions
4. Telegram notifications for signals and status updates

## Development Commands

### Environment Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with actual credentials
```

### Running the Application
```bash
# Live trading (main entry point)
python optimized_groww_trader.py

# Test telegram notifications
python telegram_notifier.py
```

### Dependencies
Core packages from `requirements.txt`:
- `growwapi==0.0.8` - Groww API client for market data and trading
- `python-telegram-bot` - Telegram bot integration
- `pandas`, `numpy` - Data processing
- `pytz` - Timezone handling for IST market hours
- `python-dotenv` - Environment configuration
- `pyotp` - TOTP authentication for Groww API

## Deployment

### AWS Lightsail Production Deployment
```bash
# Deploy to Lightsail instance
chmod +x deploy_to_lightsail.sh
./deploy_to_lightsail.sh <LIGHTSAIL_IP> ubuntu .env

# Monitor service
ssh ubuntu@<IP> 'sudo systemctl status groww-trader'
ssh ubuntu@<IP> 'sudo journalctl -u groww-trader -f'
```

### Service Configuration
- Systemd service: `deploy/groww-trader.service`
- Security hardening with restricted permissions
- Auto-restart on failure with 5-second delay
- Logging to syslog with identifier 'groww-trader'

## Critical Environment Variables

Required in `.env` file:
- `GROWW_API_KEY` - Groww trading API key
- `GROWW_SECRET_KEY` - TOTP secret for authentication
- `TELEGRAM_BOT_TOKEN` - Bot token from @BotFather
- `TELEGRAM_CHAT_ID` - Target chat ID for notifications

Optional:
- `GROWW_ACCESS_TOKEN` - Pre-generated access token (auto-generates if missing)
- `NIFTY_EXCHANGE_TOKEN` - Exchange token for NIFTY (defaults to "NIFTY")

## Market Timing and Safety

- **Market Hours**: 9:15 AM - 3:30 PM IST only
- **First 15-min Phase**: 9:15-9:30 AM for baseline capture
- **Active Trading**: 9:30 AM onwards for breakout monitoring
- **Weekend Safety**: No operations on Saturday/Sunday
- **Rate Limiting**: 1-second minimum between Telegram messages

## File Structure Context

- `deploy/` - Production deployment configurations
- `trader_one.pem` - SSH key for Lightsail deployment
- `.env` - Local environment configuration (gitignored)
- `.env.example` - Template for environment setup

## Security Considerations

- No hardcoded credentials (uses environment variables)
- Systemd security hardening in service file
- Rate limiting on external API calls
- Defensive design for educational use only

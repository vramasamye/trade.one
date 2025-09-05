# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a NIFTY Options Trading Bot that implements an automated breakout strategy using the Groww API. The bot monitors the first 15-minute candle (9:15-9:30 AM), detects breakouts, waits for retest confirmation, and sends trade alerts via Telegram.

## Key Commands

### Running the Application
```bash
python fixed_groww_trader.py  # Main trading bot (current active version)
```

### Dependencies Management
```bash
pip install -r requirements.txt  # Install all required packages
```

### Environment Setup
```bash
cp .env.example .env  # Create environment file
# Edit .env with actual credentials
```

### Deployment (AWS Lightsail)
```bash
chmod +x deploy_to_lightsail.sh
./deploy_to_lightsail.sh <LIGHTSAIL_IP> ubuntu .env
```

### Service Management (on deployed server)
```bash
sudo systemctl status groww-trader    # Check service status
sudo journalctl -u groww-trader -f    # View real-time logs
sudo systemctl restart groww-trader   # Restart service
```

## Architecture

### Core Components

1. **OptimizedGrowwTrader Class** (`fixed_groww_trader.py:51-100`)
   - Main trading logic with breakout strategy implementation
   - Manages state for first 15-minute high/low, breakouts, and retests
   - Integrates with Groww API for live market data
   - Uses threading for real-time feed processing

2. **HistoricalFetcher Class** (`historical_fetcher.py:21-75`)
   - Fetches historical candle data for first 5 and 15 minutes
   - Used when bot starts after market open to get initial levels
   - Provides fallback mechanism for missed early market data

3. **TelegramNotifier Class** (`telegram_notifier.py:16-50`)
   - Handles rate-limited message sending to Telegram
   - Includes message formatting and queue management
   - Integrates with main logger for trade alerts

### Data Flow

1. **Market Open (9:15 AM)**: Bot starts monitoring and capturing first 15-minute high/low
2. **After 9:30 AM**: Switches to 5-minute candle monitoring for breakouts
3. **Breakout Detection**: Monitors price crossing first 15-minute levels
4. **Retest Phase**: Switches to 1-minute candles after breakout
5. **Signal Generation**: Confirms entry with proper candle color and sends Telegram alert

### State Management

The bot maintains several critical state flags:
- `first15_done`: First 15-minute candle capture complete
- `breakout`: Direction of breakout ('BULLISH' or 'BEARISH')
- `retest_touch_occurred`: Price touched breakout level again
- `retest_confirmed`: Valid retest with correct candle color
- `context_set`: Historical data loaded successfully

## Environment Variables

Required in `.env` file:
```bash
GROWW_API_KEY=<your_api_key>
GROWW_SECRET_KEY=<totp_secret>
TELEGRAM_BOT_TOKEN=<bot_token>
TELEGRAM_CHAT_ID=<chat_id>
NIFTY_EXCHANGE_TOKEN=<optional_override>
```

## Integration Points

### Groww API
- Uses `growwapi` package version 0.0.8
- Handles authentication with TOTP-based access tokens
- Real-time WebSocket feed for live price updates
- Historical data API for backtesting and initial state

### Telegram Integration
- Custom `TelegramLogHandler` for log forwarding
- Rate-limited messaging with threading
- Formatted trade alerts with strike price suggestions
- Daily scheduling for market notifications

## Development Notes

- Virtual environment located in `tradevenv/`
- Main entry point is `fixed_groww_trader.py` (active version)
- Deployment configuration in `deploy/` directory
- AWS Lightsail deployment script included
- Service runs as systemd daemon on production

## Trading Strategy Implementation

The bot implements a specific intraday breakout strategy:
1. Captures first 15-minute (9:15-9:30) high/low levels
2. Monitors for 5-minute breakouts above/below these levels
3. Requires retest of breakout level for confirmation
4. Validates entry with correct candle color (green for CE, red for PE)
5. Calculates optimal strike price rounded to nearest 50 points
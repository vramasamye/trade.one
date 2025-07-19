# NIFTY Options Trading Bot

An automated trading bot that implements a breakout strategy for NIFTY options using the Groww API.

## Strategy Overview

The bot implements a specific intraday trading strategy:

1. **Mark First 15-Minute Candle**: Captures high and low of the 9:15-9:30 AM (first 15-minute) candle
2. **Monitor 5-Minute Breakouts**: From 9:30 AM onwards, monitors 5-minute candles for breakouts above/below the first 15-minute high/low
3. **Switch to 1-Minute Candles**: After breakout detection, switches to 1-minute candle monitoring
4. **Retest Confirmation**: Waits for price to retest the breakout level
5. **Entry Signal**: Confirms entry with correct color candle (Green for CE, Red for PE)
6. **Telegram Alert**: Sends trade signal with strike price suggestion to Telegram

## Files

- `live_trader.py`: Main live trading bot with real-time data processing
- `optimized_nifty_strategy_groww.py`: Strategy implementation and backtesting
- `telegram_notifier.py`: Telegram integration for trade alerts
- `requirements.txt`: Required Python packages

## Setup

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your actual credentials
   ```

3. **Required Credentials**:
   - Groww API key and secret (from https://groww.in/trade-api)
   - Telegram bot token (create via @BotFather)
   - Telegram chat ID (get via @userinfobot)

## Deployment

### AWS Lightsail Deployment

Deploy the trading bot to AWS Lightsail for 24/7 operation:

1. **Create Lightsail Instance**:
   - Launch Ubuntu 20.04 or 22.04 LTS instance
   - Choose appropriate plan (minimum $3.50/month)
   - Configure SSH key access

2. **Deploy from Local Machine**:
   ```bash
   # Make deployment script executable
   chmod +x deploy_to_lightsail.sh
   
   # Deploy to Lightsail instance
   ./deploy_to_lightsail.sh <LIGHTSAIL_IP> ubuntu .env
   ```

3. **Monitor Service**:
   ```bash
   # Check service status
   ssh ubuntu@<LIGHTSAIL_IP> 'sudo systemctl status groww-trader'
   
   # View real-time logs
   ssh ubuntu@<LIGHTSAIL_IP> 'sudo journalctl -u groww-trader -f'
   
   # Restart service if needed
   ssh ubuntu@<LIGHTSAIL_IP> 'sudo systemctl restart groww-trader'
   ```

4. **Manual Setup** (Alternative):
   - Copy `deploy/lightsail-setup.sh` to your instance
   - Run as root: `sudo bash lightsail-setup.sh`
   - Upload project files and configure manually

### Environment File (.env)
```bash
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
GROWW_API_KEY=your_groww_api_key
GROWW_SECRET_KEY=your_groww_secret_key
```

## Usage

### Live Trading
```bash
python live_trader.py
```

### Backtesting
```bash
python optimized_nifty_strategy_groww.py
```

## Strategy Details

### Entry Conditions
- **CE (Call Option)**: Bullish breakout above first 15-minute high + retest + green candle
- **PE (Put Option)**: Bearish breakout below first 15-minute low + retest + red candle

### Strike Price Selection
- Automatically calculates optimal strike price rounded to nearest 50 points
- Suggests appropriate CE/PE based on breakout direction

### Risk Management
- Only trades during market hours (9:15 AM - 3:30 PM)
- Validates retest before entry
- Requires correct color candle for confirmation
- Sends detailed trade information via Telegram

## Important Notes

- This is a defensive security tool for educational purposes
- Always validate signals before executing trades
- Consider additional risk management measures
- Test thoroughly with paper trading first
- Monitor performance and adjust parameters as needed

## Disclaimer

This software is for educational purposes only. Trading involves risk and you should consult with a financial advisor before making any investment decisions.
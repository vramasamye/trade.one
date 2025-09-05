import os
import time
import threading
import logging
from datetime import datetime
import pytz
from dotenv import load_dotenv
import pyotp
from growwapi import GrowwAPI, GrowwFeed
from telegram_notifier import send_telegram_message, send_startup_notification, _daily_scheduler
from historical_fetcher import fetch_historical_data

# Load environment variables
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GROWW_API_KEY = os.getenv("GROWW_API_KEY")
GROWW_SECRET_KEY = os.getenv("GROWW_SECRET_KEY")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ----- Telegram log handler -----
class TelegramLogHandler(logging.Handler):
    """Custom logging handler that forwards logs to Telegram asynchronously."""
    def emit(self, record):
        try:
            message = self.format(record)
            # Avoid flooding by ignoring DEBUG unless explicitly set
            if record.levelno >= logging.INFO:
                send_telegram_message(message)
        except Exception:
            # Silently ignore errors to prevent recursive logging
            pass

if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
    # _tg_handler = TelegramLogHandler(level=logging.INFO)
    # _tg_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%H:%M:%S'))
    # logger.addHandler(_tg_handler)
    pass

IST = pytz.timezone('Asia/Kolkata')

from historical_fetcher import fetch_historical_data

class OptimizedGrowwTrader:
    def __init__(self):
        # Initialize all instance variables first
        self.running = False
        self.first15_high = 0.0
        self.first15_low = float('inf')
        self.first15_done = False
        self.context_set = False  # Flag to track if we've set initial context
        self.breakout = None
        self.breakout_price = 0.0
        self.breakout_time = None
        self.retest_touch_occurred = False  # Step 1 of retest
        self.retest_confirmed = False      # Step 2 of retest
        self.retest_time = None
        self.current_price = 0.0
        self.last_update = None
        self.candle_data = []  # Store 1-minute candles
        self.tick_count = 0
        self.start_time = time.time()
        self.last_5min_log = time.time()
        self.last_15min_notification = time.time()
        self.first_feed_received = False
        self.first_5min_high = 0.0
        self.first_5min_low = float('inf')
        self.first_15min_high = 0.0
        self.first_15min_low = float('inf')
        self.first_5min_done = False
        self.first_15min_done = False
        self.feed = None
        self.groww = None
        self.feed_thread = None
        self.nifty_token = os.getenv("NIFTY_EXCHANGE_TOKEN", "NIFTY")
        self.last_signal_time = None
        self.signal_cooldown_period = 300  # 5 minutes cooldown
        self.market_open_time = datetime.strptime("09:15", "%H:%M").time()
        self.first_15_end_time = datetime.strptime("09:30", "%H:%M").time()
        self.first_15_printed = False  # Flag to track if we've printed 15-min levels at 9:31
        
        # Notification flags for timing-based alerts
        self.notification_5min_sent = False
        self.notification_15min_sent = False
        
        # Validate environment variables
        assert all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GROWW_API_KEY, GROWW_SECRET_KEY]), "Missing env vars"
        
        # Initialize Groww API
        try:
            access_token = os.getenv("GROWW_ACCESS_TOKEN")
            if not access_token:
                totp = pyotp.TOTP(GROWW_SECRET_KEY)
                access_token = GrowwAPI.get_access_token(GROWW_API_KEY, totp.now())
            self.groww = GrowwAPI(access_token)
            logger.info("GrowwAPI client initialised successfully")

            # Fetch historical data if starting after 9:30 AM
            historical_data = fetch_historical_data(self.groww)
            if historical_data:
                self.first_5min_high = historical_data.get('first_5min_high', 0)
                self.first_5min_low = historical_data.get('first_5min_low', float('inf'))
                self.first_15min_high = historical_data.get('first_15min_high', 0)
                self.first_15min_low = historical_data.get('first_15min_low', float('inf'))
                self.first_5min_done = True
                self.first_15min_done = True
                self.first15_done = True # Explicitly set this flag
                self.first15_high = self.first_15min_high
                self.first15_low = self.first_15min_low
                logger.info(f"Initialized with historical data: 15min H={self.first15_high}, L={self.first15_low}")

            # Initialize feed
            self.feed = GrowwFeed(self.groww)
            
            # Subscribe to live feed for NIFTY index
            instruments_list = [{
                "exchange": "NSE",
                "segment": "CASH",
                "exchange_token": str(self.nifty_token)
            }]
            
            self.feed.subscribe_index_value(instruments_list, on_data_received=self._on_tick)
            logger.info("Subscribed to NIFTY index feed")
            
            # Start feed in background thread
            self.running = True
            self.feed_thread = threading.Thread(target=self._run_feed, daemon=True)
            self.feed_thread.start()
            
            logger.info("Optimized Groww trader initialized")
            send_startup_notification()
            
        except Exception as e:
            self.running = False
            logger.error(f"Error initializing OptimizedGrowwTrader: {e}")
            logger.exception("Stack trace:")
            raise

    def _monitor_prices(self):
        """Background thread to monitor prices"""
        while self.running:
            try:
                current_time = datetime.now(IST)
                
                # Only monitor during market hours
                if self._is_market_hours(current_time):
                    price, timestamp = self._get_live_price()
                    
                    if price and timestamp:
                        self._process_tick(price, timestamp)
                        
                    time.sleep(1)  # 1-second updates
                else:
                    logger.debug(f"Market closed. Time: {current_time.strftime('%H:%M:%S')}")
                    time.sleep(60)  # Check every minute when market closed
                    
            except Exception as e:
                logger.error(f"Error in price monitoring: {e}")
                time.sleep(5)
    
    # =======================
    # Feed-based tick handling
    # =======================

    def _on_tick(self, data: dict):
        """Callback for GrowwFeed; processes incoming tick data"""
        try:
            # Log first feed data in detail
            if not self.first_feed_received:
                self.first_feed_received = True
                logger.info("=== FIRST FEED DATA RECEIVED ===")
                logger.info(f"Data type: {type(data)}")
                logger.info(f"Data content: {data}")
                logger.info("=== END FIRST FEED DATA ===")
            
            # Log all feed data at debug level
            logger.debug(f"Received data from feed: {data}")
            
            # Initialize variables
            price = None
            timestamp = datetime.now(IST)  # Default to current time
            
            try:
                # According to Groww SDK docs, we need to call get_index_value() to get actual price data
                index_data = self.feed.get_index_value()
                logger.debug(f"Index data from get_index_value(): {index_data}")
                
                # Parse the index data structure: NSE -> CASH -> NIFTY -> value
                if index_data and isinstance(index_data, dict):
                    # Try NSE -> CASH -> NIFTY -> value
                    nse_data = index_data.get('NSE', {})
                    if isinstance(nse_data, dict):
                        cash_data = nse_data.get('CASH', {})
                        if isinstance(cash_data, dict):
                            # Look for NIFTY data
                            nifty_data = cash_data.get('NIFTY', {})
                            if isinstance(nifty_data, dict) and 'value' in nifty_data:
                                price = float(nifty_data['value'])
                                if 'tsInMillis' in nifty_data:
                                    timestamp_ms = nifty_data['tsInMillis']
                                    timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=IST)
                                logger.debug(f"Found NIFTY price: {price}")
                            
                            # If NIFTY not found, look for any instrument with value
                            if price is None:
                                for key, value in cash_data.items():
                                    if isinstance(value, dict) and 'value' in value:
                                        price = float(value['value'])
                                        if 'tsInMillis' in value:
                                            timestamp_ms = value['tsInMillis']
                                            timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=IST)
                                        logger.info(f"Found price data in instrument: {key}")
                                        break
                
                # Log if we couldn't find price
                if price is None:
                    logger.warning(f"Could not extract price from index data. Data structure: {index_data}")
                    return
                    
            except (KeyError, ValueError, TypeError) as e:
                logger.error(f"Error parsing price data: {str(e)}\nRaw data: {data}\nException type: {type(e).__name__}")
                return
            except Exception as e:
                logger.error(f"Unexpected error parsing data: {str(e)}\nRaw data: {data}\nException type: {type(e).__name__}", exc_info=True)
                return

            # Process only during market hours
            if not self._is_market_hours(timestamp):
                logger.debug(f"Data received outside market hours. Time: {timestamp.strftime('%H:%M:%S')}")
                return

            # Log successful price extraction
            logger.debug(f"Processing tick - Price: {price:.2f}, Timestamp: {timestamp.strftime('%H:%M:%S')}")
            
            # Process the tick
            self._process_tick(price, timestamp)
            
        except Exception as e:
            logger.error(f"Critical error in _on_tick: {str(e)}\nException type: {type(e).__name__}", exc_info=True)

    def _run_feed(self):
        """Run the WebSocket feed; reconnect on failure"""
        while self.running:
            try:
                logger.info("Starting feed consumption")
                # `consume` is a blocking call that keeps the WebSocket connection alive
                self.feed.consume()
            except Exception as e:
                logger.error(f"Feed disconnected: {e}. Reconnecting in 5 seconds...")
                logger.exception("Traceback:")
                time.sleep(5)  # Wait before attempting to reconnect

    # Legacy REST method retained for reference but no longer used
    def _get_live_price(self):
        """Get live NIFTY price from Groww"""
        try:
            quote_data = self.groww.get_quote(
                exchange="NSE",
                segment="CASH",
                trading_symbol="NIFTY"
            )
            
            if quote_data and 'ltp' in quote_data:
                price = float(quote_data['ltp'])
                timestamp = datetime.now(IST)
                return price, timestamp
            
            return None, None
            
        except Exception as e:
            logger.error(f"Error fetching live price: {e}")
            return None, None
    
    def _process_tick(self, price, timestamp):
        """Process each price tick"""
        self.current_price = price
        self.last_update = timestamp
        self.tick_count += 1
        
        # Set context on first data reception
        if not self.context_set:
            self._set_initial_context(price, timestamp)
        
        # Update candle data
        self._update_candle(price, timestamp)
        
        # Execute strategy
        self._execute_strategy(timestamp)
        
        # Print 15-minute levels at 9:31 AM if not already printed
        self._print_15min_levels_at_931(timestamp)
        
        # Log performance every 100 ticks
        if self.tick_count % 100 == 0:
            uptime = time.time() - self.start_time
            tps = self.tick_count / uptime if uptime > 0 else 0
            logger.info(f"Performance: {tps:.2f} ticks/sec, Price: {price:.2f}")
    
    def _set_initial_context(self, price, timestamp):
        """Set initial context when feed data is first received"""
        self.context_set = True
        
        logger.info(f"✅ Initial context set from live data: Price={price:.2f}")
        
        # If we're restarting during market hours and 15min levels were already established,
        # display them in the initial context
        if self.first15_done and self.first15_high > 0 and self.first15_low < float('inf'):
            logger.info(f"🔄 Script restart - Previous 15min levels loaded: High={self.first15_high:.2f}, Low={self.first15_low:.2f}")
            
            # CRITICAL FIX: Check for immediate breakout when restarting with historical data
            if price > self.first15_high:
                self.breakout = 'BUY'
                self.breakout_price = price
                self.breakout_time = timestamp
                logger.info(f"🔥 IMMEDIATE BULLISH BREAKOUT DETECTED ON RESTART: {price:.2f} > {self.first15_high:.2f}")
                
                # Send immediate Telegram notification for bullish breakout - USING SAME PATTERN AS STARTUP  
                optimal_strike = round(price / 50) * 50
                breakout_message = (
                    f"🔥 *IMMEDIATE BULLISH BREAKOUT* 🔥\n\n"
                    f"📈 *Current Price:* {price:.2f}\n"
                    f"🎯 *Broke Above:* {self.first15_high:.2f}\n"
                    f"💰 *Optimal Strike:* {int(optimal_strike)} CE\n"
                    f"⏰ *Time:* {timestamp.strftime('%H:%M:%S')}\n\n"
                    f"🔍 *Next: Waiting for RETEST...*"
                )
                # Direct call, now non-blocking
                send_telegram_message(breakout_message, priority='high')
                
            elif price < self.first15_low:
                self.breakout = 'SELL'
                self.breakout_price = price
                self.breakout_time = timestamp
                logger.info(f"🔥 IMMEDIATE BEARISH BREAKOUT DETECTED ON RESTART: {price:.2f} < {self.first15_low:.2f}")
                
                # Send immediate Telegram notification for bearish breakout - USING SAME PATTERN AS STARTUP
                logger.info("🚨 ABOUT TO SEND BEARISH BREAKOUT TELEGRAM NOTIFICATION...")
                optimal_strike = round(price / 50) * 50
                breakout_message = (
                    f"🔥 *IMMEDIATE BEARISH BREAKOUT* 🔥\n\n"
                    f"📉 *Current Price:* {price:.2f}\n"
                    f"🎯 *Broke Below:* {self.first15_low:.2f}\n"
                    f"💰 *Optimal Strike:* {int(optimal_strike)} PE\n"
                    f"⏰ *Time:* {timestamp.strftime('%H:%M:%S')}\n\n"
                    f"🔍 *Next: Waiting for RETEST...*"
                )
                logger.info("🚨 CALLING send_telegram_message for BEARISH BREAKOUT...")
                send_telegram_message(breakout_message, priority='high')
                logger.info(f"🚨 send_telegram_message call completed.")
            message = (
                f"🔄 *SCRIPT RESTART DETECTED*\n\n"
                f"📡 *Feed Status:* Connected and Active\n"
                f"💹 *NIFTY Price:* {price:.2f}\n"
                f"⏰ *Time:* {timestamp.strftime('%H:%M:%S')}\n"
                f"📅 *Date:* {timestamp.strftime('%A, %B %d, %Y')}\n\n"
                f"📊 *Loaded 15min Levels (from historical data):*\n"
                f"   • High: {self.first15_high:.2f}\n"
                f"   • Low: {self.first15_low:.2f}\n"
                f"   • Range: {self.first15_high - self.first15_low:.2f} points\n\n"
                f"🎯 *Status:* Monitoring for breakouts using previous range\n"
                f"🚀 *Live monitoring activated!*"
            )
        else:
            # Send Telegram notification with current context (first message of the day from feed)
            message = (
                f"🌅 *FIRST LIVE DATA RECEIVED TODAY* 🌅\n\n"
                f"📡 *Feed Status:* Connected and Active\n"
                f"💹 *NIFTY Price:* {price:.2f}\n"
                f"⏰ *Time:* {timestamp.strftime('%H:%M:%S')}\n"
                f"📅 *Date:* {timestamp.strftime('%A, %B %d, %Y')}\n\n"
                f"📊 *Trading Context Set:*\n"
                f"   • Initial Price: {price:.2f}\n\n"
                f"🎯 *Status:* Ready to monitor breakouts\n"
                f"🚀 *Live monitoring activated!\n\n"
                f"_This is the first feed message for today_"
            )
        send_telegram_message(message, "high")
    
    def _update_candle(self, price, timestamp):
        """Update 1-minute candle data and track 5/15 minute high/low"""
        current_minute = timestamp.replace(second=0, microsecond=0)
        
        # Check if we have data for this minute
        if not self.candle_data or self.candle_data[-1]['timestamp'] != current_minute:
            # New candle
            candle = {
                'timestamp': current_minute,
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'volume': 0
            }
            self.candle_data.append(candle)
            
            # Keep only last 60 candles (1 hour)
            if len(self.candle_data) > 60:
                self.candle_data.pop(0)
            
            # Check if we need to update 5/15 minute high/low during market hours
            if self._is_market_hours(timestamp):
                # Update 5-minute high/low (first 5 minutes after market open: 9:15-9:20)
                if not self.first_5min_done and self._in_first5(timestamp):
                    self.first_5min_high = max(self.first_5min_high, price)
                    self.first_5min_low = min(self.first_5min_low, price)
                
                # Update 15-minute high/low (first 15 minutes after market open: 9:15-9:30)
                if not self.first_15min_done and self._in_first15(timestamp):
                    self.first_15min_high = max(self.first_15min_high, price)
                    self.first_15min_low = min(self.first_15min_low, price)
            
            logger.debug(f"New candle: {current_minute.strftime('%H:%M')}")
        else:
            # Update current candle
            current_candle = self.candle_data[-1]
            current_candle['high'] = max(current_candle['high'], price)
            current_candle['low'] = min(current_candle['low'], price)
            current_candle['close'] = price
            
            # Update 5/15 minute high/low for current minute during market hours
            if self._is_market_hours(timestamp):
                # Update 5-minute high/low (first 5 minutes after market open: 9:15-9:20)
                if not self.first_5min_done and self._in_first5(timestamp):
                    self.first_5min_high = max(self.first_5min_high, price)
                    self.first_5min_low = min(self.first_5min_low, price)
                
                # Update 15-minute high/low (first 15 minutes after market open: 9:15-9:30)
                if not self.first_15min_done and self._in_first15(timestamp):
                    self.first_15min_high = max(self.first_15min_high, price)
                    self.first_15min_low = min(self.first_15min_low, price)
    
    def _execute_strategy(self, timestamp):
        """Execute the breakout strategy"""
        # Phase 1: Track first 15 minutes
        if not self.first15_done:
            self._track_first_15min(timestamp)
            return
        
        # Phase 2: Detect breakout
        if not self.breakout:
            self._detect_breakout()
            return
        
        # Phase 3: Confirm retest
        if not self.retest_confirmed:
            self._confirm_retest()
            return
        
        # Phase 4: Check entry signal
        self._check_entry_signal()
    
    def _track_first_15min(self, timestamp):
        """Track first 15 minutes (9:15-9:30 AM)"""
        if self.first15_done:
            return

        if self._in_first15(timestamp):
            self.first15_high = max(self.first15_high, self.current_price)
            self.first15_low = min(self.first15_low, self.current_price)
            logger.debug(f"First 15min: H={self.first15_high:.2f}, L={self.first15_low:.2f}")
        
        # Mark completion at 9:30 AM
        elif timestamp.time() >= self.first_15_end_time:
            self.first15_done = True
            logger.info(f"✅ First 15min complete: H={self.first15_high:.2f}, L={self.first15_low:.2f}")
            
            # Send Telegram notification for first 15-minute completion
            message = (
                f"📊 *First 15-Minute Session Complete*\n\n"
                f"🔴 *High:* {self.first15_high:.2f}\n"
                f"🟢 *Low:* {self.first15_low:.2f}\n"
                f"📈 *Range:* {self.first15_high - self.first15_low:.2f} points\n"
                f"⏰ *Time:* {timestamp.strftime('%H:%M:%S')}\n\n"
                f"🎯 Now monitoring for breakouts..."
            )
            send_telegram_message(message)
    
    def _in_first15(self, timestamp):
        """Check if timestamp is in first 15 minutes (9:15-9:30 AM)"""
        return (timestamp.hour == 9 and 
                timestamp.minute >= 15 and 
                timestamp.minute < 30)
    
    def _in_first5(self, timestamp):
        """Check if timestamp is in first 5 minutes (9:15-9:20 AM)"""
        return (timestamp.hour == 9 and 
                timestamp.minute >= 15 and 
                timestamp.minute < 20)
    
    def _detect_breakout(self):
        """Detect breakout on every tick."""
        # Check for breakout
        if self.current_price > self.first15_high:
            self.breakout = 'BUY'
            self.breakout_price = self.current_price
            self.breakout_time = datetime.now(IST)
            logger.info(f"🔥 BULLISH BREAKOUT: {self.current_price:.2f} > {self.first15_high:.2f}")
            
            # Send Telegram notification for bullish breakout
            logger.info("📧 SENDING BULLISH BREAKOUT TELEGRAM NOTIFICATION...")
            optimal_strike = round(self.current_price / 50) * 50
            # Simplified message without special Markdown formatting to avoid issues
            message = (
                f"BULLISH BREAKOUT ALERT\n\n"
                f"Current Price: {self.current_price:.2f}\n"
                f"Broke Above: {self.first15_high:.2f}\n"
                f"Optimal Strike: {int(optimal_strike)} CE\n"
                f"Time: {self.breakout_time.strftime('%H:%M:%S')}\n\n"
                f"Next: Waiting for RETEST..."
            )
            logger.info(f"📧 Telegram message content: {message}")
            send_telegram_message(message, priority='high')

        elif self.current_price < self.first15_low:
            self.breakout = 'SELL'
            self.breakout_price = self.current_price
            self.breakout_time = datetime.now(IST)
            logger.info(f"🔥 BEARISH BREAKOUT: {self.current_price:.2f} < {self.first15_low:.2f}")
            
            # Send Telegram notification for bearish breakout
            logger.info("📧 SENDING BEARISH BREAKOUT TELEGRAM NOTIFICATION...")
            optimal_strike = round(self.current_price / 50) * 50
            # Simplified message without special Markdown formatting to avoid issues
            message = (
                f"BEARISH BREAKOUT ALERT\n\n"
                f"Current Price: {self.current_price:.2f}\n"
                f"Broke Below: {self.first15_low:.2f}\n"
                f"Optimal Strike: {int(optimal_strike)} PE\n"
                f"Time: {self.breakout_time.strftime('%H:%M:%S')}\n\n"
                f"Next: Waiting for RETEST..."
            )
            logger.info(f"📧 Telegram message content: {message}")
            send_telegram_message(message, priority='high')
    
    def _confirm_retest(self):
        """Confirm retest using a two-step process: touch and reclaim."""
        if not self.candle_data:
            return

        current_candle = self.candle_data[-1]

        # Step 1: Wait for the price to touch or dip below/above the breakout level
        if not self.retest_touch_occurred:
            if self.breakout == 'BUY' and current_candle['low'] <= self.first15_high:
                self.retest_touch_occurred = True
                logger.info(f"- Retest Step 1 (Touch): Price dipped to {current_candle['low']:.2f}, touching/passing 15min High of {self.first15_high:.2f}")
            elif self.breakout == 'SELL' and current_candle['high'] >= self.first15_low:
                self.retest_touch_occurred = True
                logger.info(f"- Retest Step 1 (Touch): Price rose to {current_candle['high']:.2f}, touching/passing 15min Low of {self.first15_low:.2f}")
            return # Wait for the next candle to confirm reclaim

        # Step 2: After a touch, wait for a candle to close back above/below the level
        if self.retest_touch_occurred and not self.retest_confirmed:
            if self.breakout == 'BUY' and current_candle['close'] > self.first15_high:
                self.retest_confirmed = True
                self.retest_time = datetime.now(IST)
                logger.info(f"✅ RETEST CONFIRMED (BUY): Candle closed at {current_candle['close']:.2f} back above 15min High.")
                
                # Send Telegram notification for bullish retest confirmation
                optimal_strike = round(current_candle['close'] / 50) * 50
                message = (
                    f"✅ *RETEST CONFIRMED - BULLISH* ✅\n\n"
                    f"📈 *Retest Price:* {current_candle['close']:.2f}\n"
                    f"🎯 *Reclaimed Level:* {self.first15_high:.2f}\n"
                    f"💰 *Strike Price:* {int(optimal_strike)} CE\n"
                    f"⏰ *Time:* {self.retest_time.strftime('%H:%M:%S')}\n\n"
                    f"🚀 *Next: Waiting for ENTRY SIGNAL*\n"
                    f"👀 *Need: 2 consecutive GREEN candles*"
                )
                send_telegram_message(message, "high")
                
            elif self.breakout == 'SELL' and current_candle['close'] < self.first15_low:
                self.retest_confirmed = True
                self.retest_time = datetime.now(IST)
                logger.info(f"✅ RETEST CONFIRMED (SELL): Candle closed at {current_candle['close']:.2f} back below 15min Low.")
                
                # Send Telegram notification for bearish retest confirmation
                optimal_strike = round(current_candle['close'] / 50) * 50
                message = (
                    f"✅ *RETEST CONFIRMED - BEARISH* ✅\n\n"
                    f"📉 *Retest Price:* {current_candle['close']:.2f}\n"
                    f"🎯 *Respected Level:* {self.first15_low:.2f}\n"
                    f"💰 *Strike Price:* {int(optimal_strike)} PE\n"
                    f"⏰ *Time:* {self.retest_time.strftime('%H:%M:%S')}\n\n"
                    f"🚀 *Next: Waiting for ENTRY SIGNAL*\n"
                    f"👀 *Need: 2 consecutive RED candles*"
                )
                send_telegram_message(message, "high")
    
    def _check_entry_signal(self):
        """Check for two consecutive candles that meet entry criteria."""
        if len(self.candle_data) < 3:
            return

        # Get the last two completed candles
        last_candle = self.candle_data[-2]
        prev_candle = self.candle_data[-3]

        # Define candle color conditions
        last_is_green = last_candle['close'] > last_candle['open']
        prev_is_green = prev_candle['close'] > prev_candle['open']
        last_is_red = last_candle['close'] < last_candle['open']
        prev_is_red = prev_candle['close'] < prev_candle['open']

        # Bullish entry: two consecutive green candles, both closing above 15min high
        if (self.breakout == 'BUY' and last_is_green and prev_is_green and
            last_candle['close'] > self.first15_high and 
            prev_candle['close'] > self.first15_high):
            
            logger.info("ENTRY SIGNAL: Two consecutive green candles found above 15min High.")
            self._generate_signal(last_candle)
            self._reset_strategy()

        # Bearish entry: two consecutive red candles, both closing below 15min low
        elif (self.breakout == 'SELL' and last_is_red and prev_is_red and
              last_candle['close'] < self.first15_low and 
              prev_candle['close'] < self.first15_low):

            logger.info("ENTRY SIGNAL: Two consecutive red candles found below 15min Low.")
            self._generate_signal(last_candle)
            self._reset_strategy()
    
    def _generate_signal(self, entry_candle):
        """Generate and send trade signal"""
        try:
            # Check cooldown period
            if self.last_signal_time:
                time_since_last = (datetime.now(IST) - self.last_signal_time).seconds
                if time_since_last < self.signal_cooldown_period:
                    logger.info(f"Skipping signal due to cooldown period. {self.signal_cooldown_period - time_since_last} seconds remaining")
                    return
            
            entry_price = entry_candle['close']
            optimal_strike = round(entry_price / 50) * 50
            option_type = "CE" if self.breakout == 'BUY' else "PE"
            trade_signal = f"BUY NIFTY {int(optimal_strike)} {option_type}"
            candle_color = "GREEN" if entry_candle['close'] > entry_candle['open'] else "RED"
            
            message = (
                f"🚀 OPTIMIZED NIFTY SIGNAL 🚀\n\n"
                f"**Trade:** {trade_signal}\n"
                f"**Type:** {self.breakout}\n"
                f"**Entry Price:** {entry_price:.2f}\n"
                f"**Entry Candle:** {candle_color}\n"
                f"**First 15min High:** {self.first15_high:.2f}\n"
                f"**First 15min Low:** {self.first15_low:.2f}\n"
                f"**Breakout Price:** {self.breakout_price:.2f}\n"
                f"**Breakout Time:** {self.breakout_time.strftime('%H:%M:%S')}\n"
                f"**Retest Time:** {self.retest_time.strftime('%H:%M:%S')}\n"
                f"**Signal Time:** {datetime.now(IST).strftime('%H:%M:%S')}"
            )
            
            logger.info("🚀 TRADE SIGNAL GENERATED!")
            logger.info(message)
            
            # Send telegram notification
            send_telegram_message(message)
            
            # Update last signal time
            self.last_signal_time = datetime.now(IST)
            
        except Exception as e:
            logger.error(f"Error generating signal: {e}")
    
    def _reset_strategy(self):
        """Reset strategy for next opportunity"""
        self.breakout = None
        self.breakout_price = 0.0
        self.breakout_time = None
        self.retest_touch_occurred = False
        self.retest_confirmed = False
        self.retest_time = None
        logger.info("Strategy reset for next opportunity")
    
    def _is_market_hours(self, timestamp):
        """Check if within market hours (9:15 AM to 3:30 PM)"""
        market_start = self.market_open_time
        market_end = datetime.strptime("15:30", "%H:%M").time()
        return market_start <= timestamp.time() <= market_end
    
    def get_status(self):
        """Get current status"""
        return {
            'current_price': self.current_price,
            'last_update': self.last_update.strftime('%H:%M:%S') if self.last_update else None,
            'first15_high': self.first15_high,
            'first15_low': self.first15_low,
            'first15_done': self.first15_done,
            'breakout': self.breakout,
            'breakout_price': self.breakout_price,
            'retest_confirmed': self.retest_confirmed,
            'tick_count': self.tick_count,
            'uptime': time.time() - self.start_time
        }
    
    def stop(self):
        """Stop the trader"""
        self.running = False
        try:
            if hasattr(self, "feed"):
                self.feed.close()
        except Exception as e:
            logger.error(f"Error closing feed: {e}")
        logger.info("Trader stopped")
    
    def _get_current_phase(self):
        """Get current strategy phase description"""
        if not self.first15_done:
            return "Tracking First 15min"
        elif not self.breakout:
            return "Waiting for Breakout"
        elif not self.retest_confirmed:
            return "Waiting for Retest"
        else:
            return "Waiting for Entry Signal"
    
    def _calculate_momentum(self):
        """Calculate price momentum using recent price history"""
        if len(self.price_history) < 10:
            return 0.0
        
        recent_prices = self.price_history[-10:]
        if len(recent_prices) < 2:
            return 0.0
        
        # Simple momentum calculation: (current - average) / average
        current_price = recent_prices[-1]
        avg_price = sum(recent_prices[:-1]) / len(recent_prices[:-1])
        
        return (current_price - avg_price) / avg_price if avg_price > 0 else 0.0
    
    def _analyze_volume_trend(self):
        """Analyze volume trend from recent candles
        
        Returns:
            str: Volume trend - "Increasing", "Decreasing", "Stable", "Insufficient Data", or "No Volume Data"
        """
        try:
            if len(self.candle_data) < 5:
                return "Insufficient Data"
            
            recent_volumes = [c.get('volume', 0) for c in self.candle_data[-5:]]
            if all(v == 0 for v in recent_volumes):
                return "No Volume Data"
            
            # Simple trend analysis
            early_avg = sum(recent_volumes[:2]) / 2
            late_avg = sum(recent_volumes[-2:]) / 2
            
            if late_avg > early_avg * 1.2:
                return "Increasing"
            if late_avg < early_avg * 0.8:
                return "Decreasing"
            return "Stable"
            
        except Exception as e:
            logger.error(f"Error in _analyze_volume_trend: {e}", exc_info=True)
            return "Error"
    
    def _calculate_price_strength(self):
        """Calculate price strength relative to recent range"""
        if not hasattr(self, 'price_history') or len(self.price_history) < 20:
            return 0.0
        
        recent_prices = self.price_history[-20:]
        price_high = max(recent_prices)
        price_low = min(recent_prices)
        current_price = recent_prices[-1]
        
        if price_high == price_low:
            return 0.0
        
        # Normalize to -1 to 1 range
        strength = (current_price - price_low) / (price_high - price_low)
        return (strength - 0.5) * 2  # Convert to -1 to 1 range

    def _print_15min_levels_at_931(self, timestamp):
        """Print 15-minute high/low values at 9:31 AM"""
        if (not self.first_15_printed and 
            timestamp.hour == 9 and timestamp.minute >= 31 and
            self.first15_done):
            
            self.first_15_printed = True
            logger.info(f"📊 15-Minute Levels at 9:31 AM - High: {self.first15_high:.2f}, Low: {self.first15_low:.2f}, Range: {self.first15_high - self.first15_low:.2f}")
            
            # Send Telegram notification
            message = (
                f"📊 *15-Minute Market Levels (9:31 AM Update)*\n\n"
                f"⏰ Time: {timestamp.strftime('%H:%M:%S')}\n"
                f"📈 High: {self.first15_high:.2f}\n"
                f"📉 Low: {self.first15_low:.2f}\n"
                f"📏 Range: {self.first15_high - self.first15_low:.2f} points\n\n"
                f"This is the confirmed 15-minute range for today's trading."
            )
            send_telegram_message(message)

    def _print_market_levels(self, timestamp):
        """Print 5-minute and 15-minute high/low values"""
        try:
            # Check if it's time for 5-minute notification (09:21 AM) - FIXED TIMING
            if (not self.notification_5min_sent and 
                timestamp.hour == 9 and timestamp.minute >= 21 and 
                self.first_5min_high > 0 and self.first_5min_low < float('inf')):
                
                self.notification_5min_sent = True
                optimal_strike_low = round(self.first_5min_low / 50) * 50
                optimal_strike_high = round(self.first_5min_high / 50) * 50
                message = (
                    f"📊 *5-Minute Market Levels (9:21 AM)*\n\n"
                    f"⏰ Time: {timestamp.strftime('%H:%M:%S')}\n"
                    f"📈 High: {self.first_5min_high:.2f}\n"
                    f"📉 Low: {self.first_5min_low:.2f}\n"
                    f"📏 Range: {self.first_5min_high - self.first_5min_low:.2f} points\n"
                    f"💰 Strike Range: {int(optimal_strike_low)} - {int(optimal_strike_high)}"
                )
                logger.info(f"5-Minute Levels - High: {self.first_5min_high:.2f}, Low: {self.first_5min_low:.2f}")
                send_telegram_message(message, "high")
            
            # Check if it's time for 15-minute notification (09:31 AM) - FIXED TIMING
            if (not self.notification_15min_sent and 
                timestamp.hour == 9 and timestamp.minute >= 31 and 
                self.first_15min_high > 0 and self.first_15min_low < float('inf')):
                
                self.notification_15min_sent = True
                optimal_strike_low = round(self.first_15min_low / 50) * 50
                optimal_strike_high = round(self.first_15min_high / 50) * 50
                message = (
                    f"📊 *15-Minute Market Levels (9:31 AM)*\n\n"
                    f"⏰ Time: {timestamp.strftime('%H:%M:%S')}\n"
                    f"📈 High: {self.first_15min_high:.2f}\n"
                    f"📉 Low: {self.first_15min_low:.2f}\n"
                    f"📏 Range: {self.first_15min_high - self.first_15min_low:.2f} points\n"
                    f"💰 Strike Range: {int(optimal_strike_low)} - {int(optimal_strike_high)}\n\n"
                    f"🎯 *Now monitoring for breakouts!*"
                )
                logger.info(f"15-Minute Levels - High: {self.first_15min_high:.2f}, Low: {self.first_15min_low:.2f}")
                send_telegram_message(message, "high")
                
        except Exception as e:
            logger.error(f"Error in _print_market_levels: {e}", exc_info=True)

    def _send_monitoring_notification(self):
        """Send 15-minute monitoring notification only if the feed is active."""
        try:
            status = self.get_status()
            current_time = time.time()
            
            # Only send a notification if we have received data in the last 60 seconds
            if self.last_update and (current_time - self.last_update.timestamp()) < 60:
                feed_status = "📶 Active"
                data_freshness = f"Last update: {int(current_time - self.last_update.timestamp())}s ago"
                
                # Determine strategy phase
                if not self.first15_done:
                    phase = "📊 Tracking First 15min"
                elif self.breakout:
                    phase = f"🎯 {self.breakout} Breakout Detected"
                    if self.retest_confirmed:
                        phase += " + Retest Confirmed"
                else:
                    phase = "👀 Monitoring for Breakouts"
                
                message = (
                    f"🤖 *15-Minute Monitoring Update*\n\n"
                    f"💹 *NIFTY:* {status['current_price']:.2f}\n"
                    f"📡 *Feed Status:* {feed_status}\n"
                    f"⏱️ *{data_freshness}*\n\n"
                    f"📊 *Context:*\n"
                    f"   • High: {self.first15_high:.2f}\n"
                    f"   • Low: {self.first15_low:.2f}\n"
                    f"   • Range: {self.first15_high - self.first15_low:.2f} points\n\n"
                    f"🎯 *Phase:* {phase}\n"
                    f"📈 *Ticks Processed:* {status['tick_count']:,}\n"
                    f"⏰ *Uptime:* {status['uptime']:.0f}s\n\n"
                    f"_Automated monitoring active_"
                )
                
                send_telegram_message(message)
                logger.info("📱 15-minute monitoring notification sent as feed is active.")
            else:
                logger.info("Skipping 15-minute notification as feed is inactive.")
        except Exception as e:
            logger.error(f"Error in _send_monitoring_notification: {e}", exc_info=True)


    def run(self):
        """Run the trading bot"""
        logger.info("Starting Optimized Groww Trader")
        logger.info("Monitoring NIFTY for breakout signals...")
        
        self.running = True
        self.last_5min_log = time.time()
        self.last_15min_notification = time.time()
        
        # Wait for initial price data
        logger.info("Waiting for initial price data...")
        while self.running and not hasattr(self, 'current_price'):
            time.sleep(1)
            
        if not self.running:
            return
                
        logger.info(f"Initial price received: {self.current_price:.2f}")
        
        # Initialize first feed data flag
        first_feed_processed = False
        
        # Main trading loop
        while self.running:
            try:
                current_time = time.time()
                ist_now = datetime.now(IST)
                
                # Check if we have a valid price
                if not hasattr(self, 'current_price') or self.current_price == 0.0:
                    logger.warning("No valid price data available. Waiting...")
                    time.sleep(5)
                    continue
                
                # Print first feed data if this is the first iteration after receiving data
                if not first_feed_processed:
                    first_feed_processed = True
                    logger.info("=== FIRST FEED DATA PROCESSED ===")
                    logger.info(f"Initial Price: {self.current_price:.2f}")
                    logger.info(f"Current Time: {ist_now.strftime('%Y-%m-%d %H:%M:%S')}")
                    logger.info("Waiting for market levels...")
                
                # Print market levels at scheduled times
                self._print_market_levels(ist_now)
                
                # Log status every 5 minutes
                if current_time - self.last_5min_log >= 300:  # 5 minutes
                    self.last_5min_log = current_time
                    logger.info("=== 5-Minute Status Update ===")
                    logger.info(f"Current Price: {self.current_price:.2f}")
                    logger.info(f"Phase: {self._get_current_phase()}")
                    logger.info(f"Breakout Direction: {self.breakout if hasattr(self, 'breakout') else 'None'}")
                    logger.info(f"Last Candle: {self.candle_data[-1] if self.candle_data else 'No data'}")
                    
                    # Log 5/15 minute levels if available
                    if hasattr(self, 'first_5min_done') and self.first_5min_done:
                        logger.info(f"5-Minute Levels - High: {self.first_5min_high:.2f}, Low: {self.first_5min_low:.2f}")
                    if hasattr(self, 'first_15min_done') and self.first_15min_done:
                        logger.info(f"15-Minute Levels - High: {self.first_15min_high:.2f}, Low: {self.first_15min_low:.2f}")
                
                # Send monitoring notification every 15 minutes
                if current_time - self.last_15min_notification >= 900:  # 15 minutes
                    self.last_15min_notification = current_time
                    self._send_monitoring_notification()
                
                # Sleep to prevent high CPU usage
                time.sleep(1)
                
            except KeyboardInterrupt:
                logger.info("Stopping trader...")
                self.stop()
                logger.info("Trader stopped")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                time.sleep(5)  # Prevent tight loop on error

if __name__ == "__main__":
    logger.info("Starting Optimized Groww Breakout Trader")
    
    try:
        trader = OptimizedGrowwTrader()
        trader.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
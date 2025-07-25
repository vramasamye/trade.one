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
                threading.Thread(target=send_telegram_message, args=(message,), daemon=True).start()
        except Exception:
            # Silently ignore errors to prevent recursive logging
            pass

if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
    _tg_handler = TelegramLogHandler(level=logging.INFO)
    _tg_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%H:%M:%S'))
    logger.addHandler(_tg_handler)

IST = pytz.timezone('Asia/Kolkata')

class OptimizedGrowwTrader:
    def __init__(self):
        assert all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GROWW_API_KEY, GROWW_SECRET_KEY]), "Missing env vars"
        
        # === Groww API and Feed initialisation ===
        # For index data, Groww expects the string symbol such as "NIFTY"
        self.nifty_token = os.getenv("NIFTY_EXCHANGE_TOKEN", "NIFTY")
        try:
            access_token = os.getenv("GROWW_ACCESS_TOKEN")
            if not access_token:
                totp = pyotp.TOTP(GROWW_SECRET_KEY)
                access_token = GrowwAPI.get_access_token(GROWW_API_KEY, totp.now())
            self.groww = GrowwAPI(access_token)
            logger.info("GrowwAPI client initialised successfully")
        except Exception as e:
            logger.error(f"Unable to initialise GrowwAPI: {e}")
            raise

        self.feed = GrowwFeed(self.groww)
        # Subscribe to live feed for NIFTY index using updated API method
        instruments_list = [{
            "exchange": "NSE",
            "segment": "CASH",
            "exchange_token": str(self.nifty_token)
        }]
        # `subscribe_index_value` streams live index values; callback is triggered on every update
        self.feed.subscribe_index_value(instruments_list, on_data_received=self._on_tick)

        # Start feed in background thread
        self.running = True
        self.feed_thread = threading.Thread(target=self._run_feed, daemon=True)
        self.feed_thread.start()
        
        # === Strategy state
        self.first15_high = 0.0
        self.first15_low = float('inf')
        self.first15_done = False
        self.context_set = False  # Flag to track if we've set initial context
        self.breakout = None
        self.breakout_price = 0.0
        self.breakout_time = None
        self.retest_touch_occurred = False # Step 1 of retest
        self.retest_confirmed = False      # Step 2 of retest
        self.retest_time = None
        
        # Data tracking
        self.current_price = 0.0
        self.last_update = None
        self.candle_data = []  # Store 1-minute candles
        
        # Performance tracking
        self.tick_count = 0
        self.start_time = time.time()
        

        
        logger.info("Optimized Groww trader initialized")
        send_startup_notification()

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
            token_str = str(self.nifty_token)
            tick = data.get(token_str)
            if not tick:
                return

            timestamp = datetime.fromtimestamp(
                tick['last_trade_time'] / 1000, tz=IST
            )

            # Process only during market hours
            if not self._is_market_hours(timestamp):
                return

            price = float(tick['ltp'])
            self._process_tick(price, timestamp)
        except Exception as e:
            logger.error(f"Error in _on_tick: {e}")

    def _run_feed(self):
        """Run the WebSocket feed; reconnect on failure"""
        while self.running:
            try:
                # `consume` is a blocking call that keeps the WebSocket connection alive
                self.feed.consume()
            except Exception as e:
                logger.error(f"Feed disconnected: {e}. Reconnecting in 3s")
                time.sleep(3)

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
        
        # Log performance every 100 ticks
        if self.tick_count % 100 == 0:
            uptime = time.time() - self.start_time
            tps = self.tick_count / uptime if uptime > 0 else 0
            logger.info(f"Performance: {tps:.2f} ticks/sec, Price: {price:.2f}")
    
    def _set_initial_context(self, price, timestamp):
        """Set initial context when feed data is first received"""
        self.context_set = True
        
        # Set current price as initial high/low for context
        self.first15_high = price
        self.first15_low = price
        
        # Immediately mark as done since we're setting context outside 9:15-9:30 window
        self.first15_done = True
        
        logger.info(f"✅ Initial context set from live data: Price={price:.2f}")
        
        # Send Telegram notification with current context
        message = (
            f"📊 *Trading Context Set*\n\n"
            f"🔄 *Script restarted and connected to live feed*\n"
            f"💹 *Current NIFTY Price:* {price:.2f}\n"
            f"⏰ *Time:* {timestamp.strftime('%H:%M:%S')}\n\n"
            f"📌 *Context:* Using current price as baseline\n"
            f"🎯 *Status:* Ready to monitor breakouts\n\n"
            f"_Note: This replaces the daily 9:15-9:30 AM window_"
        )
        threading.Thread(target=send_telegram_message, args=(message,), daemon=True).start()
    
    def _update_candle(self, price, timestamp):
        """Update 1-minute candle data"""
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
                
            logger.debug(f"New candle: {current_minute.strftime('%H:%M')}")
        else:
            # Update current candle
            current_candle = self.candle_data[-1]
            current_candle['high'] = max(current_candle['high'], price)
            current_candle['low'] = min(current_candle['low'], price)
            current_candle['close'] = price
    
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
        if self._in_first15(timestamp):
            self.first15_high = max(self.first15_high, self.current_price)
            self.first15_low = min(self.first15_low, self.current_price)
            logger.debug(f"First 15min: H={self.first15_high:.2f}, L={self.first15_low:.2f}")
        
        # Mark completion at 9:30 AM
        elif timestamp.time() >= datetime.strptime("09:30", "%H:%M").time():
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
            threading.Thread(target=send_telegram_message, args=(message,), daemon=True).start()
    
    def _in_first15(self, timestamp):
        """Check if timestamp is in first 15 minutes"""
        return (timestamp.hour == 9 and 
                timestamp.minute >= 15 and 
                timestamp.minute < 30)
    
    def _detect_breakout(self):
        """Detect breakout using 5-minute logic"""
        if len(self.candle_data) < 5:
            return
        
        # Get last 5 candles for 5-minute aggregation
        recent_candles = self.candle_data[-5:]
        
        # Calculate 5-minute OHLC
        candle_5min = {
            'open': recent_candles[0]['open'],
            'high': max(c['high'] for c in recent_candles),
            'low': min(c['low'] for c in recent_candles),
            'close': recent_candles[-1]['close'],
            'volume': sum(c['volume'] for c in recent_candles)
        }
        
        close_price = candle_5min['close']
        
        # Check for breakout
        if close_price > self.first15_high:
            self.breakout = 'BUY'
            self.breakout_price = close_price
            self.breakout_time = datetime.now(IST)
            logger.info(f"🔥 BULLISH BREAKOUT: {close_price:.2f} > {self.first15_high:.2f}")
            
        elif close_price < self.first15_low:
            self.breakout = 'SELL'
            self.breakout_price = close_price
            self.breakout_time = datetime.now(IST)
            logger.info(f"🔥 BEARISH BREAKOUT: {close_price:.2f} < {self.first15_low:.2f}")
    
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
            elif self.breakout == 'SELL' and current_candle['close'] < self.first15_low:
                self.retest_confirmed = True
                self.retest_time = datetime.now(IST)
                logger.info(f"✅ RETEST CONFIRMED (SELL): Candle closed at {current_candle['close']:.2f} back below 15min Low.")
    
    def _check_entry_signal(self):
        """Check for two consecutive candles that meet entry criteria."""
        if len(self.candle_data) < 2:
            return

        # Get the last two candles
        last_candle = self.candle_data[-1]
        prev_candle = self.candle_data[-2]

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
            threading.Thread(target=send_telegram_message, args=(message,), daemon=True).start()
            
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
        market_start = datetime.strptime("09:15", "%H:%M").time()
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
        """Analyze volume trend from recent candles"""
        if len(self.candle_data) < 5:
            return "Insufficient Data"
        
        recent_volumes = [c['volume'] for c in self.candle_data[-5:]]
        if all(v == 0 for v in recent_volumes):
            return "No Volume Data"
        
        # Simple trend analysis
        early_avg = sum(recent_volumes[:2]) / 2
        late_avg = sum(recent_volumes[-2:]) / 2
        
        if late_avg > early_avg * 1.2:
            return "Increasing"
        elif late_avg < early_avg * 0.8:
            return "Decreasing"
        else:
            return "Stable"
    
    def _calculate_price_strength(self):
        """Calculate price strength relative to recent range"""
        if len(self.price_history) < 20:
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

    def run(self):
        """Main run method"""
        logger.info("Starting Optimized Groww Trader")
        logger.info("Monitoring NIFTY for breakout signals...")
        
        try:
            while self.running:
                time.sleep(1)
                
                # Check for daily message every minute
                if self.tick_count % 60 == 0:
                    _daily_scheduler.send_daily_message_if_needed()
                
                # Print status every 5 minutes
                if self.tick_count % 300 == 0 and self.tick_count > 0:
                    status = self.get_status()
                    logger.info(f"Status: Price={status['current_price']:.2f}, "
                              f"Ticks={status['tick_count']}, "
                              f"Uptime={status['uptime']:.0f}s")
                
        except KeyboardInterrupt:
            logger.info("Stopping trader...")
            self.stop()

if __name__ == "__main__":
    logger.info("Starting Optimized Groww Breakout Trader")
    
    try:
        trader = OptimizedGrowwTrader()
        trader.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
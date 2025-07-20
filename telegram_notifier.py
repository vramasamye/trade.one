import os
import time
import threading
from datetime import datetime, timedelta
import pytz
import telegram
import asyncio
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Rate limiting and message queue
class TelegramNotifier:
    def __init__(self):
        self.last_message_time = None
        self.message_queue = []
        self.rate_limit_seconds = 1  # Minimum 1 second between messages
        self.lock = threading.Lock()
        
    def _can_send_message(self):
        """Check if we can send a message based on rate limiting"""
        if self.last_message_time is None:
            return True
        
        time_since_last = time.time() - self.last_message_time
        return time_since_last >= self.rate_limit_seconds
    
    def _format_message(self, message):
        """Format message for better Telegram display"""
        # Convert markdown-style formatting to Telegram's MarkdownV2
        formatted_message = message.replace('**', '*')
        return formatted_message
    
    def send_message(self, message, priority='normal'):
        """Send message with rate limiting and formatting"""
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            print("Telegram credentials not found. Please set them in your .env file.")
            return False
        
        with self.lock:
            if not self._can_send_message():
                # Add to queue if rate limited
                self.message_queue.append((message, priority, time.time()))
                return False
            
            try:
                bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
                formatted_message = self._format_message(message)
                
                # python-telegram-bot v20+ is fully async; wrap call in a short coroutine
                async def _send():
                    await bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=formatted_message,
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
                asyncio.run(_send())
                
                self.last_message_time = time.time()
                print(f"Telegram message sent successfully at {datetime.now().strftime('%H:%M:%S')}")
                return True
                
            except Exception as e:
                print(f"Error sending Telegram message: {e}")
                return False
    
    def process_queue(self):
        """Process queued messages"""
        with self.lock:
            if not self.message_queue or not self._can_send_message():
                return
            
            # Sort by priority (high priority first)
            self.message_queue.sort(key=lambda x: (x[1] != 'high', x[2]))
            
            if self.message_queue:
                message, priority, timestamp = self.message_queue.pop(0)
                self.send_message(message, priority)

# Global notifier instance
_notifier = TelegramNotifier()

def send_telegram_message(message, priority='normal'):
    """
    Enhanced Telegram message sender with rate limiting and formatting.
    
    Args:
        message (str): The message to send
        priority (str): Message priority ('high', 'normal', 'low')
    """
    global _notifier
    return _notifier.send_message(message, priority)

def process_message_queue():
    """
    Process any queued messages.
    """
    global _notifier
    _notifier.process_queue()

# Background thread to process message queue
def _queue_processor():
    """Background thread to process message queue"""
    while True:
        try:
            process_message_queue()
            time.sleep(1)  # Check queue every second
        except Exception as e:
            print(f"Error in queue processor: {e}")
            time.sleep(5)

# Start background queue processor
_queue_thread = threading.Thread(target=_queue_processor, daemon=True)
_queue_thread.start()

# Enhanced notification functions for specific events
def send_startup_notification():
    """Send trading bot startup notification"""
    message = (
        f"🚀 *NIFTY BREAKOUT TRADER STARTED* 🚀\n\n"
        f"⏰ *Time:* {datetime.now().strftime('%H:%M:%S')}\n"
        f"📊 *Status:* Live monitoring activated\n"
        f"🎯 *Strategy:* First 15min breakout with retest\n"
        f"📈 *Market:* NSE NIFTY Index\n\n"
        f"Ready to detect breakout opportunities!"
    )
    return send_telegram_message(message, priority='high')

def send_performance_update(tps, current_price, max_price, min_price, uptime, phase):
    """Send performance update notification"""
    message = (
        f"📊 *PERFORMANCE UPDATE* 📊\n\n"
        f"⚡ *Ticks/sec:* {tps:.2f}\n"
        f"💹 *Current Price:* {current_price:.2f}\n"
        f"📈 *Today's High:* {max_price:.2f}\n"
        f"📉 *Today's Low:* {min_price:.2f}\n"
        f"⏱️ *Uptime:* {uptime:.0f}s\n"
        f"🎯 *Strategy Phase:* {phase}"
    )
    return send_telegram_message(message, priority='normal')

def send_signal_notification(signal_data):
    """Send trade signal notification"""
    message = (
        f"🚀 *PREMIUM NIFTY SIGNAL* 🚀\n\n"
        f"💰 *TRADE:* {signal_data['signal']}\n"
        f"📊 *TYPE:* {signal_data['type']}\n"
        f"📈 *ENTRY:* {signal_data['entry']:.2f}\n"
        f"🛡️ *STOP LOSS:* {signal_data['stop_loss']:.2f}\n"
        f"🎯 *TARGET 1:* {signal_data['target_1']:.2f}\n"
        f"🎯 *TARGET 2:* {signal_data['target_2']:.2f}\n"
        f"⚖️ *RISK:REWARD:* 1:{signal_data['risk_reward']:.2f}\n\n"
        f"🔥 *TRADE WITH DISCIPLINE!* 🔥"
    )
    return send_telegram_message(message, priority='high')

def send_error_notification(error_msg, timestamp):
    """Send error notification"""
    message = (
        f"❌ *ERROR ALERT* ❌\n\n"
        f"*Error:* {error_msg}\n"
        f"*Time:* {timestamp}\n\n"
        f"Please check the system logs."
    )
    return send_telegram_message(message, priority='high')

def send_daily_morning_message():
    """Send daily morning greeting message"""
    message = (
        f"☀️ *GOOD DAY BOSS* ☀️\n\n"
        f"🎯 *What is your game plan today!!!* 🎯\n\n"
        f"📅 *Date:* {datetime.now().strftime('%A, %B %d, %Y')}\n"
        f"⏰ *Time:* {datetime.now().strftime('%H:%M:%S')}\n"
        f"📊 *Market Opens:* 09:15 AM\n\n"
        f"🚀 *Ready to conquer the markets!* 🚀"
    )
    return send_telegram_message(message, priority='high')

# Daily message scheduler
class DailyMessageScheduler:
    def __init__(self):
        self.last_message_date = None
        self.target_hour = 9
        self.target_minute = 0
        self.ist = pytz.timezone('Asia/Kolkata')
        
    def should_send_daily_message(self):
        """Check if we should send the daily message"""
        now = datetime.now(self.ist)
        
        # Only Monday to Friday (0=Monday, 6=Sunday)
        if now.weekday() >= 5:  # Saturday or Sunday
            return False
            
        # Check if it's 9:00 AM
        if now.hour != self.target_hour or now.minute != self.target_minute:
            return False
            
        # Check if we already sent today
        today = now.date()
        if self.last_message_date == today:
            return False
            
        return True
    
    def send_daily_message_if_needed(self):
        """Send daily message if conditions are met"""
        if self.should_send_daily_message():
            success = send_daily_morning_message()
            if success:
                self.last_message_date = datetime.now(self.ist).date()
                return True
        return False

# Global scheduler instance
_daily_scheduler = DailyMessageScheduler()
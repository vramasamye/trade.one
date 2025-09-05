import os
import time
import threading
import asyncio
from datetime import datetime
import pytz
import telegram
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

class AsyncTelegramNotifier:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._queue = asyncio.Queue()
        self._bot = None
        self.rate_limit_seconds = 1.1  # Telegram API limit is 1 msg/sec

    def start(self):
        """Starts the background event loop and consumer task."""
        if not self._thread.is_alive():
            self._thread.start()
            # Use asyncio.run_coroutine_threadsafe to start the consumer
            # This is the correct way to interact with a loop in another thread
            future = asyncio.run_coroutine_threadsafe(self._message_consumer(), self._loop)
            # You might want to handle the future result or exceptions
            print("Telegram notifier background thread started.")

    def _run_loop(self):
        """Runs the asyncio event loop."""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _format_message(self, message):
        """Formats message for Telegram, escaping special characters."""
        # More robustly escape characters for MarkdownV2
        # Basic escaping, can be improved
        escape_chars = '_*[]()~`>#+-=|{}.!'
        for char in escape_chars:
            message = message.replace(char, f'\{char}')
        return message

    async def _message_consumer(self):
        """The consumer task that sends messages from the queue."""
        print("Message consumer started.")
        if not TELEGRAM_BOT_TOKEN:
            print("Telegram bot token not found.")
            return
            
        self._bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
        
        while True:
            try:
                message, priority = await self._queue.get()
                
                start_time = time.monotonic()
                
                try:
                    # No need to format here if we use MarkdownV2 and escape properly
                    await self._bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=message,
                        parse_mode='MarkdownV2',
                        disable_web_page_preview=True
                    )
                    print(f"Telegram message sent successfully at {datetime.now().strftime('%H:%M:%S')}")
                except telegram.error.BadRequest as e:
                    # If MarkdownV2 fails, try sending as plain text
                    print(f"MarkdownV2 failed: {e}. Retrying with plain text.")
                    await self._bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=message,
                        disable_web_page_preview=True
                    )
                except Exception as e:
                    print(f"Error sending Telegram message: {e}")
                
                self._queue.task_done()
                
                # Enforce rate limit
                elapsed = time.monotonic() - start_time
                if elapsed < self.rate_limit_seconds:
                    await asyncio.sleep(self.rate_limit_seconds - elapsed)
                    
            except Exception as e:
                print(f"Critical error in message consumer: {e}")
                # Avoid exiting the loop on error
                await asyncio.sleep(5)

    def send_message(self, message, priority='normal'):
        """
        Thread-safe method to queue a message for sending.
        Returns True if queued, False if credentials are not set.
        """
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            print("Telegram credentials not found. Please set them in your .env file.")
            return False
        
        # Use a thread-safe way to put items in the queue
        # The loop is running in another thread, so we need to be careful
        formatted_message = self._format_message(message)
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (formatted_message, priority))
        
        return True # Message is queued, not necessarily sent

# --- Global Instance and Interface ---

_notifier = AsyncTelegramNotifier()
_notifier.start()

def send_telegram_message(message, priority='normal'):
    """

    Queues a message to be sent asynchronously via Telegram.
    
    Args:
        message (str): The message to send.
        priority (str): Message priority ('high', 'normal', 'low'). Not yet implemented in queue logic, but preserved.
    
    Returns:
        bool: True if the message was successfully queued, False otherwise.
    """
    return _notifier.send_message(message, priority)

# The process_message_queue and _queue_processor are no longer needed
# as the new async notifier handles this internally.


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
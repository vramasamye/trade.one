
import os
import datetime
import logging
from typing import Dict
from dotenv import load_dotenv
import pytz
from growwapi import GrowwAPI

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

IST = pytz.timezone('Asia/Kolkata')

class HistoricalFetcher:
    def __init__(self, groww_api_instance):
        self.groww = groww_api_instance

    def get_market_open_time(self, trading_date: str = None) -> str:
        if trading_date is None:
            trading_date = datetime.datetime.now(IST).strftime("%Y-%m-%d")
        return f"{trading_date} 09:15:00"

    def get_first_n_minutes_data(
        self, 
        symbol: str = "NIFTY",
        exchange: str = "NSE", 
        segment: str = "CASH",
        minutes: int = 5
    ) -> Dict:
        if not self.groww:
            logger.error("GrowwAPI not available")
            return {}

        market_open = self.get_market_open_time()
        open_time = datetime.datetime.strptime(market_open, "%Y-%m-%d %H:%M:%S")
        end_time = open_time + datetime.timedelta(minutes=minutes)
        end_time_str = end_time.strftime("%Y-%m-%d %H:%M:%S")

        try:
            logger.info(f"Fetching {minutes}-minute historical data for {symbol}...")
            response = self.groww.get_historical_candle_data(
                trading_symbol=symbol,
                exchange=self.groww.EXCHANGE_NSE,
                segment=self.groww.SEGMENT_CASH,
                start_time=market_open,
                end_time=end_time_str,
                interval_in_minutes=1
            )

            if 'candles' not in response or not response['candles']:
                logger.warning(f"No historical data available for {symbol}")
                return {}

            candles = response['candles']
            highs = [float(candle[2]) for candle in candles]
            lows = [float(candle[3]) for candle in candles]

            result = {
                "high": max(highs),
                "low": min(lows),
            }
            logger.info(f"Successfully fetched {minutes}-minute data for {symbol}: High={result['high']}, Low={result['low']}")
            return result

        except Exception as e:
            logger.error(f"Error fetching historical data for {symbol}: {e}")
            return {}

def fetch_historical_data(groww_api_instance) -> Dict:
    fetcher = HistoricalFetcher(groww_api_instance)
    now = datetime.datetime.now(IST)

    if now.time() >= datetime.time(9, 30):
        logger.info("Fetching historical data for first 5 and 15 minutes...")
        data_5min = fetcher.get_first_n_minutes_data(minutes=5)
        data_15min = fetcher.get_first_n_minutes_data(minutes=15)

        return {
            "first_5min_high": data_5min.get("high", 0),
            "first_5min_low": data_5min.get("low", float('inf')),
            "first_15min_high": data_15min.get("high", 0),
            "first_15min_low": data_15min.get("low", float('inf')),
        }
    return {}

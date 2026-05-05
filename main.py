import sys
import os
import time
import json
import logging
from dotenv import load_dotenv

# Path setup
sys.path.append(os.path.dirname(os.path.realpath(__file__)))

from utils.capital_api import CapitalClient
import user_data.strategies.harmonic_strategy as harmonic_module
# Import other strategy modules here as you create them

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def main():
    load_dotenv()

    def load_config():
        root_dir = os.path.dirname(os.path.realpath(__file__))
        config_path = os.path.join(root_dir, "config.json")
        defaults = {
            "selected_strategy": "HarmonicStrategy",
            "watch_pairs": [
                "BTCUSD",
                "ETHUSD",
                "LTCUSD",
                "XRPUSD",
                "BCHUSD",
                "ADAUSD",
                "DOTUSD",
                "LINKUSD",
                "SOLUSD",
                "BNBUSD",
            ]
        }

        if not os.path.exists(config_path):
            with open(config_path, "w") as config_file:
                json.dump(defaults, config_file, indent=4)
            logger.warning("config.json not found. Created default config.json.")
            return defaults

        try:
            with open(config_path, "r") as config_file:
                config = json.load(config_file)
                if not isinstance(config.get("watch_pairs"), list):
                    config["watch_pairs"] = defaults["watch_pairs"]
                if not config.get("selected_strategy"):
                    config["selected_strategy"] = defaults["selected_strategy"]
                return config
        except Exception as e:
            logger.error(f"Error reading config.json: {e}")
            return defaults

    config = load_config()
    strategy_name = config.get("selected_strategy", "HarmonicStrategy")
    
    strategy_map = {
        "HarmonicStrategy": harmonic_module.HarmonicStrategy,
        # "MovingAverageStrategy": ma_module.MAStrategy, <— Example for later
    }
    
    try:
        StrategyClass = strategy_map[strategy_name]
        strategy = StrategyClass()
        logger.info(f"Using strategy: {strategy_name}")
    except KeyError:
        logger.error(f"Strategy {strategy_name} not found in strategy_map!")
        return

    # --- API Setup ---
    client = CapitalClient(
        os.getenv("CAPITAL_API_KEY"),
        os.getenv("CAPITAL_IDENTIFIER"),
        os.getenv("CAPITAL_PASSWORD"),
        demo=True
    )

    if not client.login():
        logger.error("Login failed.")
        return

    watch_pairs = config.get("watch_pairs", [])
    if not watch_pairs:
        logger.error("No trading pairs configured in config.json. Exiting.")
        return

    watch_pairs = [pair.strip().upper() for pair in watch_pairs if isinstance(pair, str) and pair.strip()]
    logger.info(f"Watching trading pairs: {', '.join(watch_pairs)}")

    while True:
        try:
            for market_symbol in watch_pairs:
                prices = client.get_prices(market_symbol, count=50)
                if not prices:
                    logger.warning(f"No price data for {market_symbol}. Skipping.")
                    continue

                signal = strategy.analyze(prices)
                if isinstance(signal, dict):
                    logger.info(
                        f"Analysis complete. Market: {market_symbol} | Pattern: {signal.get('pattern')} | "
                        f"Direction: {signal.get('direction')} | Entry: {signal.get('entry_price')} | "
                        f"StopLoss: {signal.get('stop_loss')} | TrailingStop: {signal.get('trailing_stop')}"
                    )
                else:
                    logger.info(f"Analysis complete. Market: {market_symbol} | Signal: {signal}")

            time.sleep(60)

        except Exception as e:
            logger.error(f"Loop error: {e}")
            time.sleep(10) # Short sleep before retry

if __name__ == "__main__":
    main()
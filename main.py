import sys
import os
import time
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
    
    # --- Universal Strategy Loader ---
    strategy_name = os.getenv("SELECTED_STRATEGY", "HarmonicStrategy")
    
    # Map the string name to the actual class
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

    while True:
        try:
            market_symbol = "BTCUSD"
            prices = client.get_prices(market_symbol, count=50)
            
            if prices:
                # All strategies must have an .analyze() method now
                signal = strategy.analyze(prices)
                logger.info(f"Analysis complete. Market: {market_symbol} | Signal: {signal}")
            
            time.sleep(60)
            
        except Exception as e:
            logger.error(f"Loop error: {e}")
            time.sleep(10) # Short sleep before retry

if __name__ == "__main__":
    main()
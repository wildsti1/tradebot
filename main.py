import sys
import os
import time
import json
import logging
import threading
from dotenv import load_dotenv

# Path setup
sys.path.append(os.path.dirname(os.path.realpath(__file__)))

from utils.capital_api import CapitalClient
import user_data.strategies.harmonic_strategy as harmonic_module
import user_data.strategies.consolidation_breakout_strategy as breakout_module
# Import other strategy modules here as you create them
from web import app, run_web_server, balance_info, open_positions, closed_trades

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))

def calc_size_from_risk(balance, entry_price, stop_level, risk_pct, min_size, max_size):
    """
    Simple risk sizing consistent with this bot's PnL math:
      pnl ~= (exit - entry) * size  (or inverse for SHORT)

    Risk amount = balance * risk_pct/100
    stop_distance = abs(entry - stop)
    size = risk_amount / stop_distance
    """
    try:
        balance = float(balance)
        entry_price = float(entry_price)
        stop_level = float(stop_level)
        risk_pct = float(risk_pct)
        min_size = float(min_size)
        max_size = float(max_size)
    except (TypeError, ValueError):
        return None

    if balance <= 0 or risk_pct <= 0:
        return None

    stop_distance = abs(entry_price - stop_level)
    if stop_distance <= 0:
        return None

    risk_amount = balance * (risk_pct / 100.0)
    size = risk_amount / stop_distance
    return clamp(size, min_size, max_size)

class Position:
    def __init__(self, symbol, direction, size, entry_price, stop_loss, take_profit, deal_id):
        self.symbol = symbol
        self.direction = direction
        self.size = size
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.deal_id = deal_id

class Trade:
    def __init__(self, symbol, direction, entry_price, exit_price, pnl, reason):
        self.symbol = symbol
        self.direction = direction
        self.entry_price = entry_price
        self.exit_price = exit_price
        self.pnl = pnl
        self.reason = reason


def parse_trade_history_item(item):
    symbol = item.get('symbol') or item.get('epic') or item.get('instrument')
    direction = item.get('direction') or item.get('tradeDirection') or item.get('positionType')
    if direction and direction.upper() in ('BUY', 'SELL'):
        direction = 'LONG' if direction.upper() == 'BUY' else 'SHORT'

    entry_price = item.get('entryPrice') or item.get('openPrice') or item.get('open') or item.get('price')
    exit_price = item.get('exitPrice') or item.get('closePrice') or item.get('close')
    pnl = item.get('profit') or item.get('pnl') or item.get('gain') or 0.0
    reason = item.get('reason') or item.get('closeReason') or item.get('status') or 'history'

    try:
        entry_price = float(entry_price)
    except (TypeError, ValueError):
        entry_price = 0.0

    try:
        exit_price = float(exit_price)
    except (TypeError, ValueError):
        exit_price = 0.0

    try:
        pnl = float(pnl)
    except (TypeError, ValueError):
        pnl = 0.0

    return Trade(symbol or 'UNKNOWN', direction or 'LONG', entry_price, exit_price, pnl, reason)


def load_trade_history(client):
    history = client.get_trade_history()
    if not history:
        logger.info('No trade history returned from Capital API.')
        return

    history_items = []
    if isinstance(history, dict):
        for key in ('history', 'trades', 'data', 'positions'):
            if key in history and isinstance(history[key], list):
                history_items = history[key]
                break
        if not history_items and isinstance(history.get('accounts'), list):
            history_items = history['accounts']
    elif isinstance(history, list):
        history_items = history

    if not history_items:
        logger.info('Trade history response was empty or unsupported format.')
        return

    imported = 0
    for item in history_items:
        if not isinstance(item, dict):
            continue
        trade = parse_trade_history_item(item)
        closed_trades.append(trade)
        imported += 1

    logger.info(f'Loaded {imported} historical trades from Capital API.')


def main():
    load_dotenv()

    def load_config():
        root_dir = os.path.dirname(os.path.realpath(__file__))
        config_path = os.path.join(root_dir, "config.json")
        defaults = {
            "selected_strategy": "ConsolidationBreakoutStrategy",
            "watch_pairs": [
                "EURUSD"
            ],
            "risk": {
                "risk_per_trade_pct": 1.0,
                "min_size": 0.01,
                "max_size": 5.0
            }
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
                if not isinstance(config.get("risk"), dict):
                    config["risk"] = defaults["risk"]
                else:
                    for k, v in defaults["risk"].items():
                        if k not in config["risk"]:
                            config["risk"][k] = v

                # Backwards compatibility: allow legacy `risk_pct` at top-level
                # - if <= 1, treat as fraction (0.3 => 30%)
                # - else treat as percent
                if "risk_pct" in config and isinstance(config.get("risk"), dict):
                    try:
                        legacy = float(config.get("risk_pct"))
                        if legacy <= 1:
                            config["risk"]["risk_per_trade_pct"] = legacy * 100.0
                        else:
                            config["risk"]["risk_per_trade_pct"] = legacy
                    except (TypeError, ValueError):
                        pass
                return config
        except Exception as e:
            logger.error(f"Error reading config.json: {e}")
            return defaults

    config = load_config()
    strategy_name = config.get("selected_strategy", "ConsolidationBreakoutStrategy")
    
    strategy_map = {
        "HarmonicStrategy": harmonic_module.HarmonicStrategy,
        "ConsolidationBreakoutStrategy": breakout_module.ConsolidationBreakoutStrategy,
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

    load_trade_history(client)

    # Start Flask web server in a separate thread
    flask_thread = threading.Thread(target=run_web_server, daemon=True)
    flask_thread.start()
    logger.info("Web server started on http://0.0.0.0:5000")
    logger.info("Trading bot is online and ready to receive requests.")

    watch_pairs = config.get("watch_pairs", [])
    if not watch_pairs:
        logger.error("No trading pairs configured in config.json. Exiting.")
        return

    risk_cfg = config.get("risk") or {}
    try:
        risk_per_trade_pct = float(risk_cfg.get("risk_per_trade_pct", 1.0))
    except (TypeError, ValueError):
        risk_per_trade_pct = 1.0
    try:
        min_size = float(risk_cfg.get("min_size", 0.01))
    except (TypeError, ValueError):
        min_size = 0.01
    try:
        max_size = float(risk_cfg.get("max_size", 5.0))
    except (TypeError, ValueError):
        max_size = 5.0

    risk_per_trade_pct = clamp(risk_per_trade_pct, 0.0, 100.0)
    min_size = max(0.0, min_size)
    max_size = max(min_size, max_size)
    if risk_per_trade_pct >= 30:
        logger.warning("Risk per trade is set to >= 30%% of balance. This is extremely risky.")

    watch_pairs = [pair.strip().upper() for pair in watch_pairs if isinstance(pair, str) and pair.strip()]
    logger.info(f"Watching trading pairs: {', '.join(watch_pairs)}")

    while True:
        try:
            # Update balance
            accounts = client.get_accounts()
            if accounts and 'accounts' in accounts:
                acc = accounts['accounts'][0]  # Assume first account
                try:
                    # Balance is nested: acc['balance']['balance'] and acc['balance']['available']
                    if isinstance(acc.get('balance'), dict):
                        balance_info['balance'] = float(acc['balance'].get('balance', 0))
                        balance_info['available'] = float(acc['balance'].get('available', 0))
                    else:
                        balance_info['balance'] = float(acc.get('balance', 0))
                        balance_info['available'] = float(acc.get('available', 0))
                except (ValueError, TypeError, KeyError) as e:
                    logger.warning(f"Could not parse balance data: {e}")
                    balance_info['balance'] = 0.0
                    balance_info['available'] = 0.0

            for market_symbol in watch_pairs:
                prices = client.get_prices(market_symbol, count=50)
                if not prices:
                    logger.warning(f"No price data for {market_symbol}. Skipping.")
                    continue

                signal = strategy.analyze(prices)
                if isinstance(signal, dict):
                    if signal.get('signal') == 'OPEN_POSITION':
                        # Open position
                        direction = 'BUY' if signal['direction'] == 'LONG' else 'SELL'
                        stop_level = signal.get('stop_loss')
                        limit_level = signal.get('take_profit')
                        entry_price = signal.get('entry_price')

                        size = None
                        if stop_level is not None and entry_price is not None:
                            # Prefer available balance for risk sizing (more conservative than total balance).
                            sizing_balance = balance_info.get('available', 0) or balance_info.get('balance', 0)
                            size = calc_size_from_risk(
                                sizing_balance,
                                entry_price,
                                stop_level,
                                risk_per_trade_pct,
                                min_size,
                                max_size,
                            )
                        if size is None or size <= 0:
                            size = min_size

                        result = client.open_position(market_symbol, direction, size, stop_level, limit_level)
                        if result and 'dealReference' in result:
                            deal_id = result['dealReference']
                            pos = Position(market_symbol, signal['direction'], size, entry_price, stop_level, limit_level, deal_id)
                            open_positions[market_symbol] = pos
                            logger.info(f"Opened position: {market_symbol} {direction} size={size} at {entry_price}")
                        else:
                            logger.error(f"Failed to open position for {market_symbol}")
                    elif signal.get('signal') == 'CLOSE_POSITION':
                        # Close position
                        if market_symbol in open_positions:
                            pos = open_positions[market_symbol]
                            result = client.close_position(pos.deal_id)
                            if result:
                                # Get exit price from current prices
                                current_price = prices['prices'][0]['close'] if prices['prices'] else pos.entry_price
                                pnl = (current_price - pos.entry_price) * pos.size if pos.direction == 'LONG' else (pos.entry_price - current_price) * pos.size
                                trade = Trade(market_symbol, pos.direction, pos.entry_price, current_price, pnl, signal.get('reason', 'Manual'))
                                closed_trades.append(trade)
                                del open_positions[market_symbol]
                                logger.info(f"Closed position: {market_symbol} PnL: {pnl}")
                            else:
                                logger.error(f"Failed to close position for {market_symbol}")
                    
                    logger.info(
                        f"Analysis complete. Market: {market_symbol} | Pattern: {signal.get('pattern')} | "
                        f"Direction: {signal.get('direction')} | Entry: {signal.get('entry_price')} | "
                        f"StopLoss: {signal.get('stop_loss')} | TakeProfit: {signal.get('take_profit')}"
                    )
                else:
                    logger.info(f"Analysis complete. Market: {market_symbol} | Signal: {signal}")

            time.sleep(60)

        except Exception as e:
            logger.error(f"Loop error: {e}")
            time.sleep(10)  # Short sleep before retry

if __name__ == "__main__":
    main()

import sys
import os
import time
import json
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

SOFIA_TZ = ZoneInfo('Europe/Sofia')

# ==========================================================
# ⚙️ QUICK SETTINGS SECTION (ADJUST HERE)
# ==========================================================
DEFAULT_TIMEFRAME = '15MINUTE'     # Options: 'MINUTE', '5MINUTE', '15MINUTE', 'HOUR', 'DAY'
PRICE_COUNT = 100             # Bars to fetch (Strategy lookback needs at least 10-20)
LOOP_INTERVAL_SEC = 60        # Seconds to wait between market checks
# NOTE: Demo / live mode is controlled by environment or config, not this file.
# ==========================================================

# Path setup
sys.path.append(os.path.dirname(os.path.realpath(__file__)))

from utils.capital_api import CapitalClient
import user_data.strategies.harmonic_strategy as harmonic_module
import user_data.strategies.consolidation_breakout_strategy as breakout_module
from web import app, run_web_server, balance_info, open_positions, closed_trades, capital_api_stats, set_strategy_and_pairs
from datetime import datetime
from zoneinfo import ZoneInfo

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

TRADE_LOG_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "user_data", "logs", "trades")

def ensure_trade_log_dir():
    if not os.path.exists(TRADE_LOG_DIR):
        os.makedirs(TRADE_LOG_DIR, exist_ok=True)


def write_trade_log(event_type, data):
    ensure_trade_log_dir()
    timestamp = datetime.now(SOFIA_TZ).strftime("%Y%m%d_%H%M%S")
    symbol = data.get('symbol', 'unknown').replace('/', '_')
    filename = f"trade_{event_type.lower()}_{symbol}_{timestamp}.json"
    file_path = os.path.join(TRADE_LOG_DIR, filename)
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump({
                'event_type': event_type,
                'timestamp': datetime.now(SOFIA_TZ).isoformat(),
                'data': data
            }, f, indent=4)
        logger.info(f"Trade log saved: {file_path}")
    except Exception as e:
        logger.error(f"Failed to write trade log {file_path}: {e}")

# --- Helper Classes & Functions ---

def clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


def parse_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'y', 'on')


def track_capital_api_request():
    capital_api_stats['count'] += 1
    capital_api_stats['last_request'] = datetime.now(SOFIA_TZ)


def get_dynamic_spread_from_prices(prices):
    """Calculate spread from the latest price bar returned by the API."""
    try:
        data = prices['prices'] if isinstance(prices, dict) and 'prices' in prices else prices
        last = data[-1]
        close_price = last.get('closePrice') or last.get('Close')
        if not isinstance(close_price, dict):
            return None

        ask = close_price.get('ask') or close_price.get('Ask')
        bid = close_price.get('bid') or close_price.get('Bid')
        if ask is None or bid is None:
            return None

        return abs(float(ask) - float(bid))
    except Exception:
        return None


def get_effective_entry_price(prices, direction):
    """Return current ask for BUY or bid for SELL."""
    try:
        data = prices['prices'] if isinstance(prices, dict) and 'prices' in prices else prices
        last = data[-1]
        close_price = last.get('closePrice') or last.get('Close')
        if not isinstance(close_price, dict):
            return None

        ask = close_price.get('ask') or close_price.get('Ask')
        bid = close_price.get('bid') or close_price.get('Bid')
        if direction == 'BUY' and ask is not None:
            return float(ask)
        if direction == 'SELL' and bid is not None:
            return float(bid)
        return None
    except Exception:
        return None

def get_effective_exit_price(prices, position_direction):
    """
    Exit price uses the opposite side of the spread:
      - LONG exits at bid (SELL)
      - SHORT exits at ask (BUY)
    """
    try:
        data = prices['prices'] if isinstance(prices, dict) and 'prices' in prices else prices
        last = data[-1]
        close_price = last.get('closePrice') or last.get('Close')
        if not isinstance(close_price, dict):
            return None

        ask = close_price.get('ask') or close_price.get('Ask')
        bid = close_price.get('bid') or close_price.get('Bid')
        if position_direction == 'LONG' and bid is not None:
            return float(bid)
        if position_direction == 'SHORT' and ask is not None:
            return float(ask)
        return None
    except Exception:
        return None

def is_capital_api_error(response):
    return isinstance(response, dict) and bool(response.get("errorCode"))


def calc_size_from_risk(balance, entry_price, stop_level, risk_pct, min_size, max_size):
    """Calculates position size based on balance and stop distance."""
    try:
        balance, entry_price = float(balance), float(entry_price)
        stop_level, risk_pct = float(stop_level), float(risk_pct)
        min_size, max_size = float(min_size), float(max_size)
        
        if balance <= 0 or risk_pct <= 0: return None
        
        stop_distance = abs(entry_price - stop_level)
        if stop_distance <= 0: return None

        risk_amount = balance * (risk_pct / 100.0)
        size = risk_amount / stop_distance
        
        # Rounding to 5 decimals to avoid API errors
        return round(clamp(size, min_size, max_size), 5)
    except:
        return None

class Position:
    def __init__(self, symbol, direction, size, entry_price, stop_loss, take_profit, deal_id, spread=0.0):
        self.symbol = symbol
        self.direction = direction
        self.size = size
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.deal_id = deal_id
        self.spread = spread or 0.0

class Trade:
    def __init__(self, symbol, direction, entry_price, exit_price, pnl, reason):
        self.symbol = symbol
        self.direction = direction
        self.entry_price = entry_price
        self.exit_price = exit_price
        self.pnl = pnl
        self.reason = reason

def parse_trade_history_item(item):
    symbol = item.get('symbol') or item.get('epic')
    direction = item.get('direction') or item.get('tradeDirection')
    if direction and direction.upper() in ('BUY', 'SELL'):
        direction = 'LONG' if direction.upper() == 'BUY' else 'SHORT'
    
    entry_price = float(item.get('openPrice') or 0)
    exit_price = float(item.get('closePrice') or 0)
    pnl = float(item.get('profit') or 0)
    reason = item.get('reason') or 'history'
    
    return Trade(symbol or 'UNKNOWN', direction or 'LONG', entry_price, exit_price, pnl, reason)

def load_trade_history(client):
    track_capital_api_request()
    history = client.get_trade_history()
    if not history:
        logger.info("Trade history not loaded (disabled or unavailable).")
        return
    
    items = []
    if isinstance(history, dict):
        for key in ('history', 'trades', 'data'):
            if key in history and isinstance(history[key], list):
                items = history[key]
                break
    elif isinstance(history, list):
        items = history

    for item in items:
        if isinstance(item, dict):
            closed_trades.append(parse_trade_history_item(item))
    logger.info(f"Loaded {len(items)} historical trades.")

# --- Core Logic ---

def main():
    load_dotenv()
    strategy_debug = os.getenv("TRADEBOT_STRATEGY_DEBUG", "0") == "1"

    def load_config():
        root_dir = os.path.dirname(os.path.realpath(__file__))
        config_path = os.path.join(root_dir, "config.json")
        defaults = {
            "selected_strategy": "ConsolidationBreakoutStrategy",
            "watch_pairs": ["EURUSD", "AUDUSD"],
            "risk": {"risk_per_trade_pct": 30.0, "min_size": 0.001, "max_size": 1.0}
        }
        if not os.path.exists(config_path):
            with open(config_path, "w") as f: json.dump(defaults, f, indent=4)
            return defaults
        with open(config_path, "r") as f: return json.load(f)

    config = load_config()
    strategy_map = {
        "HarmonicStrategy": harmonic_module.HarmonicStrategy,
        "ConsolidationBreakoutStrategy": breakout_module.ConsolidationBreakoutStrategy,
    }
    
    strategy_name = config.get("selected_strategy")
    strategy = strategy_map.get(strategy_name, breakout_module.ConsolidationBreakoutStrategy)()
    logger.info(f"Initialized Strategy: {strategy_name} | Timeframe: {DEFAULT_TIMEFRAME}")

    demo_mode = os.getenv("CAPITAL_DEMO")
    if demo_mode is None:
        demo_mode = config.get("demo")
    is_demo = parse_bool(demo_mode, default=True)
    logger.info(f"Running in {'DEMO' if is_demo else 'LIVE'} mode")

    client = CapitalClient(
        os.getenv("CAPITAL_API_KEY"),
        os.getenv("CAPITAL_IDENTIFIER"),
        os.getenv("CAPITAL_PASSWORD"),
        demo=is_demo
    )

    track_capital_api_request()
    if not client.login():
        logger.error("API Login Failed.")
        return

    load_trade_history(client)
    threading.Thread(target=run_web_server, daemon=True).start()
    
    watch_pairs = [p.upper() for p in config.get("watch_pairs", [])]
    set_strategy_and_pairs(strategy_name, watch_pairs)
    risk_cfg = config.get("risk", {})
    risk_pct = float(risk_cfg.get("risk_per_trade_pct", 1.0))
    min_sz, max_sz = float(risk_cfg.get("min_size", 0.01)), float(risk_cfg.get("max_size", 1.0))

    while True:
        try:
            # 1. Update Balance
            track_capital_api_request()
            acc_data = client.get_accounts()
            if acc_data and 'accounts' in acc_data:
                acc = acc_data['accounts'][0]
                balance_info['balance'] = float(acc['balance'].get('balance', 0))
                balance_info['available'] = float(acc['balance'].get('available', 0))

            # 2. Iterate Pairs
            for symbol in watch_pairs:
                track_capital_api_request()
                prices = client.get_prices(symbol, count=PRICE_COUNT, resolution=DEFAULT_TIMEFRAME)
                if not prices: continue

                signal = strategy.analyze(prices)
                if not isinstance(signal, dict):
                    if strategy_debug and getattr(strategy, "last_debug", None):
                        logger.info(f"Analysis complete. Market: {symbol} | Signal: None | Debug: {strategy.last_debug}")
                    continue

                # --- Execute Signal ---
                if signal.get('signal') == 'OPEN_POSITION':
                    direction = 'BUY' if signal['direction'] == 'LONG' else 'SELL'
                    entry, sl, tp = signal['entry_price'], signal['stop_loss'], signal['take_profit']

                    effective_entry = get_effective_entry_price(prices, direction) or entry
                    current_spread = get_dynamic_spread_from_prices(prices) or 0.0
                    size = calc_size_from_risk(balance_info['available'], effective_entry, sl, risk_pct, min_sz, max_sz)
                    if size is None:
                        logger.warning(f"Invalid position size for {symbol} | entry={effective_entry} sl={sl}")
                        continue

                    track_capital_api_request()
                    res = client.open_position(symbol, direction, size, sl, tp)
                    if res and 'dealReference' in res:
                        open_positions[symbol] = Position(
                            symbol,
                            signal['direction'],
                            size,
                            effective_entry,
                            sl,
                            tp,
                            res['dealReference'],
                            spread=current_spread
                        )
                        log_data = {
                            'symbol': symbol,
                            'direction': signal['direction'],
                            'order_type': direction,
                            'size': size,
                            'entry_price': effective_entry,
                            'stop_loss': sl,
                            'take_profit': tp,
                            'spread': current_spread,
                            'deal_id': res['dealReference'],
                            'balance_available': balance_info.get('available', 0),
                            'risk_pct': risk_pct,
                            'api_response': res
                        }
                        write_trade_log('OPEN_POSITION', log_data)
                        logger.info(
                            f"🚀 {direction} {symbol} | Size: {size} | Entry: {effective_entry} | Spread: {current_spread:.5f} | SL: {sl}"
                        )

                elif signal.get('signal') == 'CLOSE_POSITION' and symbol in open_positions:
                    pos = open_positions[symbol]
                    exit_price = get_effective_exit_price(prices, pos.direction) or pos.entry_price
                    exit_spread = get_dynamic_spread_from_prices(prices) or 0.0

                    track_capital_api_request()
                    close_res = client.close_position(pos.deal_id)
                    if close_res and not is_capital_api_error(close_res):
                        # PnL estimate using side-correct entry/exit prices; this already includes spread impact.
                        pnl = (exit_price - pos.entry_price) * pos.size if pos.direction == 'LONG' else (pos.entry_price - exit_price) * pos.size
                        spread_cost_est = None
                        try:
                            # Optional breakdown: half-spread on entry + half-spread on exit.
                            spread_cost_est = ((float(pos.spread) / 2.0) + (float(exit_spread) / 2.0)) * float(pos.size)
                        except Exception:
                            spread_cost_est = None

                        closed_trades.append(Trade(pos.symbol, pos.direction, pos.entry_price, exit_price, pnl, signal.get('reason')))

                        logger.info(
                            f"💰 Closed {symbol} | Reason: {signal.get('reason')} | Exit: {exit_price} | "
                            f"PnL(est): {pnl:.4f} | Spread(entry/exit): {pos.spread:.5f}/{exit_spread:.5f}"
                        )
                        write_trade_log('CLOSE_POSITION', {
                            'symbol': pos.symbol,
                            'direction': pos.direction,
                            'deal_id': pos.deal_id,
                            'entry_price': pos.entry_price,
                            'exit_price': exit_price,
                            'pnl_estimate': pnl,
                            'exit_reason': signal.get('reason'),
                            'stop_loss': pos.stop_loss,
                            'take_profit': pos.take_profit,
                            'spread_entry': pos.spread,
                            'spread_exit': exit_spread,
                            'spread_cost_estimate': spread_cost_est,
                            'api_response': close_res
                        })
                        del open_positions[symbol]
                    elif close_res and is_capital_api_error(close_res):
                        logger.warning(f"Failed to close {symbol} deal_id={pos.deal_id} errorCode={close_res.get('errorCode')} details={close_res}")
                    else:
                        logger.warning(f"Failed to close {symbol} deal_id={pos.deal_id} (no response)")

            time.sleep(LOOP_INTERVAL_SEC)

        except Exception as e:
            logger.error(f"Main Loop Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()

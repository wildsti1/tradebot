import sys
import os
import time
import json
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import inspect

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
# import user_data.strategies.harmonic_strategy as harmonic_module
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

def _to_float_or_none(value):
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None

def _deep_find_first(obj, wanted_keys, max_nodes=2000):
    """
    Breadth-first search through dict/list payloads to find the first value for any key in wanted_keys.
    Returns the raw value (not coerced).
    """
    if obj is None:
        return None
    wanted = set(wanted_keys)
    queue = [obj]
    seen = 0
    while queue and seen < max_nodes:
        current = queue.pop(0)
        seen += 1
        if isinstance(current, dict):
            for k, v in current.items():
                if k in wanted and v is not None:
                    return v
                if isinstance(v, (dict, list)):
                    queue.append(v)
        elif isinstance(current, list):
            for v in current:
                if isinstance(v, (dict, list)):
                    queue.append(v)
    return None

def parse_capital_positions(payload):
    """
    Best-effort parser for Capital.com /positions response.
    Returns dict keyed by symbol/epic with normalized fields.
    """
    if not isinstance(payload, dict):
        return {}
    items = payload.get("positions")
    if not isinstance(items, list):
        return {}

    parsed = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        market = item.get("market") if isinstance(item.get("market"), dict) else {}
        pos = item.get("position") if isinstance(item.get("position"), dict) else item

        symbol = (market.get("epic") or item.get("epic") or item.get("symbol"))
        if not symbol:
            continue

        raw_dir = (pos.get("direction") or item.get("direction") or _deep_find_first(item, ["direction"]))
        direction = None
        if isinstance(raw_dir, str):
            d = raw_dir.strip().upper()
            if d == "BUY":
                direction = "LONG"
            elif d == "SELL":
                direction = "SHORT"
            elif d in ("LONG", "SHORT"):
                direction = d

        size = _to_float_or_none(
            pos.get("size")
            or item.get("size")
            or _deep_find_first(item, ["size"])
        ) or 0.0
        entry_price = _to_float_or_none(
            pos.get("level")
            or pos.get("openLevel")
            or item.get("level")
            or item.get("openLevel")
            or _deep_find_first(item, ["level", "openLevel", "openPrice"])
        ) or 0.0
        stop_loss = _to_float_or_none(
            pos.get("stopLevel")
            or item.get("stopLevel")
            or _deep_find_first(item, ["stopLevel", "stopLossLevel"])
        )
        take_profit = _to_float_or_none(
            pos.get("limitLevel")
            or item.get("limitLevel")
            or _deep_find_first(item, ["limitLevel", "takeProfitLevel", "takeProfit", "tpLevel"])
        )
        deal_id = (
            pos.get("dealId")
            or item.get("dealId")
            or _deep_find_first(item, ["dealId"])
            or pos.get("dealReference")
            or item.get("dealReference")
            or _deep_find_first(item, ["dealReference"])
        )

        parsed[symbol] = Position(
            symbol=symbol,
            direction=direction or "LONG",
            size=size,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            deal_id=deal_id,
            spread=0.0,
        )

    return parsed

def sync_open_positions_from_broker(client):
    """
    Keeps UI state aligned with broker state, including broker-closed positions (TP/SL).
    """
    track_capital_api_request()
    payload = client.get_positions()
    if not payload or is_capital_api_error(payload):
        return

    if os.getenv("TRADEBOT_POSITIONS_DEBUG", "0") == "1":
        try:
            if isinstance(payload, dict):
                sample = None
                if isinstance(payload.get("positions"), list) and payload["positions"]:
                    sample = payload["positions"][0]
                logger.info(
                    "Positions debug | keys=%s sample=%s",
                    list(payload.keys())[:40],
                    json.dumps(sample, default=str)[:1200],
                )
            else:
                logger.info("Positions debug | type=%s sample=%s", type(payload).__name__, str(payload)[:1200])
        except Exception as e:
            logger.info("Positions debug logging failed: %s", e)

    broker_positions = parse_capital_positions(payload)
    # Drop positions that disappeared on the broker (closed externally / by TP/SL)
    for symbol in list(open_positions.keys()):
        if symbol not in broker_positions:
            del open_positions[symbol]
    # Add/update positions that exist on the broker
    for symbol, pos in broker_positions.items():
        open_positions[symbol] = pos


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
    order_debug = os.getenv("TRADEBOT_ORDER_DEBUG", "0") == "1"
    positions_sync_interval_sec = int(os.getenv("TRADEBOT_POSITIONS_SYNC_SEC", "15") or 15)
    last_positions_sync_ts = 0.0

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
        # "HarmonicStrategy": harmonic_module.HarmonicStrategy,
        "ConsolidationBreakoutStrategy": breakout_module.ConsolidationBreakoutStrategy,
    }
    
    strategy_name = config.get("selected_strategy")
    strategy_cls = strategy_map.get(strategy_name, breakout_module.ConsolidationBreakoutStrategy)

    def _filter_init_kwargs(cls, kwargs):
        if not isinstance(kwargs, dict):
            return {}
        try:
            sig = inspect.signature(cls.__init__)
            allowed = {p.name for p in sig.parameters.values() if p.name not in ("self", "args", "kwargs")}
            return {k: v for k, v in kwargs.items() if k in allowed}
        except Exception:
            return dict(kwargs)

    def _strategy_params_for(symbol):
        """
        Optional per-pair strategy params in config.json:

        {
          "selected_strategy": "ConsolidationBreakoutStrategy",
          "strategy_params": {
            "ConsolidationBreakoutStrategy": {
              "default": {"tp_sl_pct": 0.5, "lookback": 10},
              "pairs": {"BTCUSD": {"tp_sl_pct": 1.0}}
            }
          }
        }
        """
        all_params = config.get("strategy_params", {})
        strat_params = all_params.get(strategy_name) if isinstance(all_params, dict) else None
        if not isinstance(strat_params, dict):
            return {}
        default_params = strat_params.get("default", {})
        pairs = strat_params.get("pairs", {})
        pair_params = pairs.get(symbol, {}) if isinstance(pairs, dict) else {}
        merged = {}
        if isinstance(default_params, dict):
            merged.update(default_params)
        if isinstance(pair_params, dict):
            merged.update(pair_params)
        return _filter_init_kwargs(strategy_cls, merged)

    # Separate strategy instance per symbol so each pair can keep its own state and params.
    strategies_by_symbol = {}
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
    # Support both config formats:
    # - New: {"risk": {"risk_per_trade_pct": 15, "min_size": 0.001, "max_size": 1.0}}
    # - Legacy/simple: {"risk_pct": 0.15} (fraction) or {"risk_pct": 15} (percent)
    if not isinstance(risk_cfg, dict):
        risk_cfg = {}

    raw_risk = None
    if "risk_per_trade_pct" in risk_cfg:
        raw_risk = risk_cfg.get("risk_per_trade_pct")
    elif "risk_pct" in config:
        raw_risk = config.get("risk_pct")
    else:
        raw_risk = 1.0

    try:
        raw_risk = float(raw_risk)
    except Exception:
        raw_risk = 1.0

    # If user supplies 0 < risk <= 1, treat it as fraction of balance (0.15 => 15%).
    risk_pct = (raw_risk * 100.0) if (0 < raw_risk <= 1.0) else raw_risk

    min_sz = float(risk_cfg.get("min_size", 0.01))
    max_sz = float(risk_cfg.get("max_size", 1.0))

    while True:
        try:
            # 1. Update Balance
            track_capital_api_request()
            acc_data = client.get_accounts()
            if acc_data and 'accounts' in acc_data:
                acc = acc_data['accounts'][0]
                balance_info['balance'] = float(acc['balance'].get('balance', 0))
                balance_info['available'] = float(acc['balance'].get('available', 0))

            # 1b. Sync positions from broker (handles broker-closed positions)
            now_ts = time.time()
            if positions_sync_interval_sec > 0 and (now_ts - last_positions_sync_ts) >= positions_sync_interval_sec:
                sync_open_positions_from_broker(client)
                last_positions_sync_ts = now_ts

            # 2. Iterate Pairs
            for symbol in watch_pairs:
                if symbol not in strategies_by_symbol:
                    params = _strategy_params_for(symbol)
                    strategies_by_symbol[symbol] = strategy_cls(**params)
                    if params:
                        logger.info(f"Strategy params for {symbol}: {params}")

                strategy = strategies_by_symbol[symbol]
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
                    entry = signal.get('entry_price')
                    sl = signal.get('stop_loss')
                    tp = signal.get('take_profit')
                    if entry is None or sl is None:
                        logger.warning(f"Invalid trade plan for {symbol}: missing entry_price/stop_loss | signal={signal}")
                        continue

                    effective_entry = get_effective_entry_price(prices, direction) or entry
                    current_spread = get_dynamic_spread_from_prices(prices) or 0.0

                    # Strategies typically compute TP/SL around a mid/last close.
                    # The broker fills at bid/ask, so recompute absolute levels around the effective entry
                    # while preserving the strategy's intended distances.
                    try:
                        entry_f = float(entry)
                        effective_entry_f = float(effective_entry)
                        sl_f = float(sl)
                        stop_dist = abs(entry_f - sl_f)
                        tp_dist = abs(float(tp) - entry_f) if tp is not None else None
                        if signal.get('direction') == 'LONG':
                            sl = round(effective_entry_f - stop_dist, 8)
                            if tp_dist is not None:
                                tp = round(effective_entry_f + tp_dist, 8)
                        else:
                            sl = round(effective_entry_f + stop_dist, 8)
                            if tp_dist is not None:
                                tp = round(effective_entry_f - tp_dist, 8)
                    except Exception:
                        pass

                    size = calc_size_from_risk(balance_info['available'], effective_entry, sl, risk_pct, min_sz, max_sz)
                    if size is None:
                        logger.warning(f"Invalid position size for {symbol} | entry={effective_entry} sl={sl}")
                        continue

                    if order_debug:
                        try:
                            logger.info(
                                "Order plan | %s %s | entry(strategy)=%.8f entry(effective)=%.8f spread=%.5f | "
                                "SL=%.8f TP=%s | risk_pct=%.4f available=%.2f size=%.5f",
                                direction,
                                symbol,
                                float(entry),
                                float(effective_entry),
                                float(current_spread),
                                float(sl),
                                ("%.8f" % float(tp)) if tp is not None else "None",
                                float(risk_pct),
                                float(balance_info.get("available", 0)),
                                float(size),
                            )
                        except Exception:
                            logger.info("Order plan | %s %s | entry=%s effective_entry=%s SL=%s TP=%s size=%s risk_pct=%s",
                                        direction, symbol, entry, effective_entry, sl, tp, size, risk_pct)

                    track_capital_api_request()
                    res = client.open_position(symbol, direction, size, sl, tp)
                    if res and 'dealReference' in res:
                        deal_ref = res.get('dealReference')
                        deal_id = deal_ref
                        try:
                            track_capital_api_request()
                            conf = client.get_confirmation(deal_ref)
                            if isinstance(conf, dict) and conf.get("dealId"):
                                deal_id = conf.get("dealId")
                            if order_debug and isinstance(conf, dict):
                                try:
                                    logger.info(
                                        "Order confirm | %s %s | dealId=%s status=%s reason=%s | stopLevel=%s limitLevel=%s",
                                        direction,
                                        symbol,
                                        conf.get("dealId") or deal_id,
                                        conf.get("dealStatus") or conf.get("status"),
                                        conf.get("reason"),
                                        conf.get("stopLevel"),
                                        conf.get("limitLevel"),
                                    )
                                except Exception:
                                    logger.info("Order confirm | %s %s | %s", direction, symbol, str(conf)[:800])
                        except Exception:
                            deal_id = deal_ref

                        open_positions[symbol] = Position(
                            symbol,
                            signal['direction'],
                            size,
                            effective_entry,
                            sl,
                            tp,
                            deal_id,
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
                            'deal_id': deal_id,
                            'deal_reference': deal_ref,
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
                        # Ensure UI stays in sync even if broker closed additional legs/positions.
                        sync_open_positions_from_broker(client)
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

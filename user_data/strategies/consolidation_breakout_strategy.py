import os
import pandas as pd
import numpy as np

# --- Consolidation Breakout Strategy Settings ---
# Adjust these values to control the strategy parameters.
#
# lookback: number of bars to look back for consolidation check.
# consolidation_pct: percentage range for consolidation (e.g., 0.1 = 10%).
# tp_sl_pct: take profit/stop loss percentage (e.g., 0.2 = 20%).
# start_hour: start hour for trading (9 = 9:00).
# end_hour: end hour for trading (19 = 19:00).
# cost_per_trade: commission per trade.
DEFAULT_LOOKBACK = 10
DEFAULT_CONSOLIDATION_PCT = 0.5
DEFAULT_TP_SL_PCT = 0.5
DEFAULT_START_HOUR = 0
DEFAULT_END_HOUR = 23
# Capital.com primarily charges via spread (no separate commission for most CFDs),
# so keep this at 0 and model costs from bid/ask spread in execution/PnL instead.
DEFAULT_COST_PER_TRADE = 0.0

class ConsolidationBreakoutStrategy:
    """Consolidation breakout strategy with time filtering and TP/SL."""

    def __init__(self, lookback=DEFAULT_LOOKBACK, consolidation_pct=DEFAULT_CONSOLIDATION_PCT,
                 tp_sl_pct=DEFAULT_TP_SL_PCT, start_hour=DEFAULT_START_HOUR,
                 end_hour=DEFAULT_END_HOUR, cost_per_trade=DEFAULT_COST_PER_TRADE):
        self.lookback = lookback
        self.consolidation_pct = consolidation_pct
        self.tp_sl_pct = tp_sl_pct
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.cost_per_trade = cost_per_trade
        self.position = None
        self.entry_price = 0.0
        self.entry_index = 0
        self.debug = os.getenv("TRADEBOT_STRATEGY_DEBUG", "0") == "1"
        self.last_debug = {}

    def _set_debug(self, **kwargs):
        if not self.debug:
            return
        self.last_debug = kwargs

    def _format_trade_plan(self, direction, entry_price):
        # tp_sl_pct is in percent (e.g. 0.5 => 0.5%)
        pct = self.tp_sl_pct / 100.0
        stop_loss = entry_price * (1 - pct) if direction == "LONG" else entry_price * (1 + pct)
        take_profit = entry_price * (1 + pct) if direction == "LONG" else entry_price * (1 - pct)
        return {
            "signal": "OPEN_POSITION",
            "pattern": "Consolidation Breakout",
            "direction": direction,
            "entry_price": round(entry_price, 8),
            "stop_loss": round(stop_loss, 8),
            "take_profit": round(take_profit, 8)
        }

    def _as_float(self, value):
        if value is None:
            return None
        if isinstance(value, (int, float, np.number)):
            return float(value)
        if isinstance(value, dict):
            bid = value.get('bid')
            ask = value.get('ask')
            last = value.get('lastTraded')
            if bid is not None and ask is not None:
                try:
                    return (float(bid) + float(ask)) / 2.0
                except (TypeError, ValueError):
                    pass
            if last is not None:
                try:
                    return float(last)
                except (TypeError, ValueError):
                    pass
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return None

    def _normalize_prices_df(self, price_data):
        """
        Normalizes incoming candle data into a DataFrame with:
          Datetime, High, Low, Close

        Supports Capital.com style:
          { "prices": [ { "snapshotTimeUTC": "...", "highPrice": {...}, ... } ] }
        """
        if isinstance(price_data, dict) and 'prices' in price_data:
            rows = price_data.get('prices') or []
        elif isinstance(price_data, list):
            rows = price_data
        else:
            if isinstance(price_data, dict):
                keys = list(price_data.keys())
                self._set_debug(
                    reason="missing_prices_key",
                    keys=keys[:20],
                    errorCode=price_data.get("errorCode"),
                    error=price_data.get("error"),
                )
            return None

        if not rows:
            if isinstance(price_data, dict):
                self._set_debug(
                    reason="empty_prices",
                    errorCode=price_data.get("errorCode"),
                    error=price_data.get("error"),
                )
            else:
                self._set_debug(reason="empty_prices")
            return None

        normalized = []
        for row in rows:
            if not isinstance(row, dict):
                continue

            dt = (
                row.get('Datetime')
                or row.get('datetime')
                or row.get('snapshotTimeUTC')
                or row.get('snapshotTime')
                or row.get('time')
                or row.get('timestamp')
            )

            high = row.get('High') or row.get('high') or row.get('highPrice')
            low = row.get('Low') or row.get('low') or row.get('lowPrice')
            close = row.get('Close') or row.get('close') or row.get('closePrice')

            high_f = self._as_float(high)
            low_f = self._as_float(low)
            close_f = self._as_float(close)

            if dt is None or high_f is None or low_f is None or close_f is None:
                continue

            normalized.append({'Datetime': dt, 'High': high_f, 'Low': low_f, 'Close': close_f})

        if not normalized:
            return None

        df = pd.DataFrame(normalized)
        df['Datetime'] = pd.to_datetime(df['Datetime'], errors='coerce', utc=True)
        df = df.dropna(subset=['Datetime', 'High', 'Low', 'Close'])
        if df.empty:
            return None
        df = df.sort_values('Datetime').reset_index(drop=True)
        return df

    def analyze(self, price_data):
        """
        The entry point called by main.py.
        Analyzes the latest price data for consolidation breakout signals.
        """
        try:
            df = self._normalize_prices_df(price_data)
            if df is None:
                self._set_debug(reason="normalize_failed")
                return None

            highs = df['High'].values
            lows = df['Low'].values
            closes = df['Close'].values
            dates = df['Datetime']

            # Get the latest bar
            i = len(df) - 1
            if i < self.lookback + 1:
                self._set_debug(reason="not_enough_bars", bars=len(df), lookback=self.lookback)
                return None

            current_hour = dates.iloc[i].hour

            if self.position is None:
                # Check if within trading hours
                if self.start_hour <= current_hour <= self.end_hour:
                    h_prev = np.max(highs[i - self.lookback:i])
                    l_prev = np.min(lows[i - self.lookback:i])
                    is_consol = (h_prev * (1 - self.consolidation_pct / 100)) <= l_prev

                    if is_consol:
                        if highs[i] > h_prev:
                            self.position = 'long'
                            self.entry_price = closes[i]
                            self.entry_index = i
                            self._set_debug(reason="open_long", time=str(dates.iloc[i]), h_prev=float(h_prev), l_prev=float(l_prev))
                            return self._format_trade_plan("LONG", self.entry_price)
                        elif lows[i] < l_prev:
                            self.position = 'short'
                            self.entry_price = closes[i]
                            self.entry_index = i
                            self._set_debug(reason="open_short", time=str(dates.iloc[i]), h_prev=float(h_prev), l_prev=float(l_prev))
                            return self._format_trade_plan("SHORT", self.entry_price)
                        self._set_debug(reason="consolidating_no_breakout", time=str(dates.iloc[i]), h_prev=float(h_prev), l_prev=float(l_prev))
                        return None
                    self._set_debug(reason="not_consolidating", time=str(dates.iloc[i]), h_prev=float(h_prev), l_prev=float(l_prev))
                    return None
                self._set_debug(reason="outside_trading_hours", time=str(dates.iloc[i]), current_hour=int(current_hour))
                return None
            else:
                # Check for exit
                h_since = np.max(highs[self.entry_index:i+1])
                l_since = np.min(lows[self.entry_index:i+1])

                if self.position == 'long':
                    if h_since >= self.entry_price * (1 + self.tp_sl_pct / 100):
                        # Take profit
                        self.position = None
                        self._set_debug(reason="close_long_tp", time=str(dates.iloc[i]))
                        return {"signal": "CLOSE_POSITION", "reason": "Take Profit"}
                    elif l_since <= self.entry_price * (1 - self.tp_sl_pct / 100):
                        # Stop loss
                        self.position = None
                        self._set_debug(reason="close_long_sl", time=str(dates.iloc[i]))
                        return {"signal": "CLOSE_POSITION", "reason": "Stop Loss"}
                elif self.position == 'short':
                    if l_since <= self.entry_price * (1 - self.tp_sl_pct / 100):
                        # Take profit
                        self.position = None
                        self._set_debug(reason="close_short_tp", time=str(dates.iloc[i]))
                        return {"signal": "CLOSE_POSITION", "reason": "Take Profit"}
                    elif h_since >= self.entry_price * (1 + self.tp_sl_pct / 100):
                        # Stop loss
                        self.position = None
                        self._set_debug(reason="close_short_sl", time=str(dates.iloc[i]))
                        return {"signal": "CLOSE_POSITION", "reason": "Stop Loss"}

                self._set_debug(reason="in_position_no_exit", time=str(dates.iloc[i]), position=self.position)
            return None

        except Exception as e:
            print(f"Error in ConsolidationBreakoutStrategy: {e}")
            self._set_debug(reason="exception", error=str(e))
            return None

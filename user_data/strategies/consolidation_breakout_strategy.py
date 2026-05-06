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
DEFAULT_CONSOLIDATION_PCT = 0.2
DEFAULT_TP_SL_PCT = 0.4
DEFAULT_START_HOUR = 9
DEFAULT_END_HOUR = 19
DEFAULT_COST_PER_TRADE = 0.006

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

    def _format_trade_plan(self, direction, entry_price):
        stop_loss = entry_price * (1 - self.tp_sl_pct) if direction == "LONG" else entry_price * (1 + self.tp_sl_pct)
        take_profit = entry_price * (1 + self.tp_sl_pct) if direction == "LONG" else entry_price * (1 - self.tp_sl_pct)
        return {
            "signal": "OPEN_POSITION",
            "pattern": "Consolidation Breakout",
            "direction": direction,
            "entry_price": round(entry_price, 8),
            "stop_loss": round(stop_loss, 8),
            "take_profit": round(take_profit, 8)
        }

    def analyze(self, price_data):
        """
        The entry point called by main.py.
        Analyzes the latest price data for consolidation breakout signals.
        """
        try:
            # Convert to DataFrame
            if isinstance(price_data, dict) and 'prices' in price_data:
                df = pd.DataFrame(price_data['prices'])
            elif isinstance(price_data, list):
                df = pd.DataFrame(price_data)
            else:
                return None

            # Assume columns: Datetime, Close, High, Low, Open, Volume
            required_cols = ['High', 'Low', 'Close', 'Datetime']
            if not all(col in df.columns for col in required_cols):
                return None

            df['Datetime'] = pd.to_datetime(df['Datetime'])
            df = df.sort_values('Datetime').reset_index(drop=True)

            highs = df['High'].values
            lows = df['Low'].values
            closes = df['Close'].values
            dates = df['Datetime']

            # Get the latest bar
            i = len(df) - 1
            if i < self.lookback + 1:
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
                            return self._format_trade_plan("LONG", self.entry_price)
                        elif lows[i] < l_prev:
                            self.position = 'short'
                            self.entry_price = closes[i]
                            self.entry_index = i
                            return self._format_trade_plan("SHORT", self.entry_price)
            else:
                # Check for exit
                h_since = np.max(highs[self.entry_index:i+1])
                l_since = np.min(lows[self.entry_index:i+1])

                if self.position == 'long':
                    if h_since >= self.entry_price * (1 + self.tp_sl_pct / 100):
                        # Take profit
                        self.position = None
                        return {"signal": "CLOSE_POSITION", "reason": "Take Profit"}
                    elif l_since <= self.entry_price * (1 - self.tp_sl_pct / 100):
                        # Stop loss
                        self.position = None
                        return {"signal": "CLOSE_POSITION", "reason": "Stop Loss"}
                elif self.position == 'short':
                    if l_since <= self.entry_price * (1 - self.tp_sl_pct / 100):
                        # Take profit
                        self.position = None
                        return {"signal": "CLOSE_POSITION", "reason": "Take Profit"}
                    elif h_since >= self.entry_price * (1 + self.tp_sl_pct / 100):
                        # Stop loss
                        self.position = None
                        return {"signal": "CLOSE_POSITION", "reason": "Stop Loss"}

            return None

        except Exception as e:
            print(f"Error in ConsolidationBreakoutStrategy: {e}")
            return None
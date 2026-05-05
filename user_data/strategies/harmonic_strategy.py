import numpy as np
from scipy.signal import argrelextrema
import pandas as pd

# --- Harmonic Strategy Settings ---
# Adjust these values to control pattern sensitivity and risk parameters.
#
# error_tolerance: how closely the price moves must match harmonic ratios.
#   Use a smaller value for tighter pattern matching, larger for more flexibility.
# stop_loss_pct: percent distance from entry to place a stop-loss order.
#   e.g. 0.02 = 2% below entry for LONG trades, 2% above entry for SHORT trades.
# trailing_stop_pct: percent distance for a trailing stop.
#   The trailing stop follows the price by this percentage after entry.
DEFAULT_ERROR_TOLERANCE = 0.05
DEFAULT_STOP_LOSS_PCT = 0.02
DEFAULT_TRAILING_STOP_PCT = 0.02

class HarmonicStrategy:
    """Harmonic pattern strategy with stop-loss and trailing-stop trade planning."""

    def __init__(self, error_tolerance=DEFAULT_ERROR_TOLERANCE, stop_loss_pct=DEFAULT_STOP_LOSS_PCT, trailing_stop_pct=DEFAULT_TRAILING_STOP_PCT):
        self.error_tolerance = error_tolerance
        self.stop_loss_pct = stop_loss_pct
        self.trailing_stop_pct = trailing_stop_pct
        self.min_data_points = 21

    def _format_trade_plan(self, direction, entry_price, pattern):
        stop_loss = entry_price * (1 - self.stop_loss_pct) if direction == "LONG" else entry_price * (1 + self.stop_loss_pct)
        trailing_stop = entry_price * (1 - self.trailing_stop_pct) if direction == "LONG" else entry_price * (1 + self.trailing_stop_pct)
        return {
            "signal": "OPEN_POSITION",
            "pattern": pattern,
            "direction": direction,
            "entry_price": round(entry_price, 8),
            "stop_loss": round(stop_loss, 8),
            "trailing_stop": round(trailing_stop, 8)
        }

    def _determine_direction(self, X, A, B, C, D):
        return "SHORT" if D > C else "LONG"

    def analyze(self, price_data):
        """
        The entry point called by main.py.
        Converts raw API list into a DataFrame and runs analysis.
        """
        try:
            # 1. Convert Capital.com API prices to DataFrame
            # Capital.com format usually has prices in 'prices' key
            if isinstance(price_data, dict) and 'prices' in price_data:
                raw_list = price_data['prices']
            else:
                raw_list = price_data

            # Extract 'closePrice' -> 'ask' or 'bid'
            df = pd.DataFrame([
                {'Close': float(p['closePrice']['ask'])} for p in raw_list
            ])

            if len(df) < self.min_data_points:
                return "WAITING_FOR_DATA"

            # 2. Find Peaks/Valleys (ZigZag)
            points = self.find_peaks(df)

            # 3. Analyze for patterns
            pattern = self.analyze_patterns(points)
            if not pattern:
                return "NO_PATTERN"

            entry_price = float(df.Close.iloc[-1])
            direction = self._determine_direction(*points[-5:])
            return self._format_trade_plan(direction, entry_price, pattern)

        except Exception as e:
            return f"STRATEGY_ERROR: {str(e)}"

    def find_peaks(self, df):
        # order=10 means a point must be the highest/lowest among 21 candles
        # Using .values for speed
        close_vals = df.Close.values
        
        peaks = argrelextrema(close_vals, np.greater_equal, order=10)[0]
        valleys = argrelextrema(close_vals, np.less_equal, order=10)[0]
        
        # Combine, sort by index, and get prices
        all_indices = sorted(np.concatenate([peaks, valleys]))
        pts = close_vals[all_indices]
        return pts

    def analyze_patterns(self, points):
        if len(points) < 5:
            return None
        
        X, A, B, C, D = points[-5:]
        
        XA = abs(A - X)
        AB = abs(B - A)
        BC = abs(C - B)
        CD = abs(D - C)
        
        if XA == 0 or AB == 0 or BC == 0: return None
        
        retr_AB_XA = AB / XA
        retr_BC_AB = BC / AB
        retr_CD_BC = CD / BC
        
        # Gartley
        if (abs(retr_AB_XA - 0.618) <= self.error_tolerance and 0.382 <= retr_BC_AB <= 0.886):
            return "Gartley"
            
        # Bat
        if ((abs(retr_AB_XA - 0.382) <= self.error_tolerance or abs(retr_AB_XA - 0.50) <= self.error_tolerance) and 0.382 <= retr_BC_AB <= 0.886):
            return "Bat"

        # Butterfly
        if (abs(retr_AB_XA - 0.786) <= self.error_tolerance and 0.382 <= retr_BC_AB <= 0.886):
            return "Butterfly"

        # Crab
        if (0.382 <= retr_AB_XA <= 0.618 and 0.382 <= retr_BC_AB <= 0.886 and retr_CD_BC >= 2.24):
            return "Crab"

        return None
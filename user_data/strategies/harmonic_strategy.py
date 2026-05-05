import numpy as np
from scipy.signal import argrelextrema
import pandas as pd

class HarmonicStrategy:
    def __init__(self, error_tolerance=0.05):
        self.error_tolerance = error_tolerance

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

            if len(df) < 21: # 'order=10' needs at least 21 points to find a peak
                return "WAITING_FOR_DATA"

            # 2. Find Peaks/Valleys (ZigZag)
            points = self.find_peaks(df)
            
            # 3. Analyze for patterns
            pattern = self.analyze_patterns(points)
            
            return pattern if pattern else "NO_PATTERN"

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
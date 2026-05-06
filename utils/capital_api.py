import os
import requests
import json
import logging

class CapitalClient:
    def __init__(self, api_key, identifier, password, demo=True):
        self.api_key = api_key
        self.identifier = identifier
        self.password = password
        self.base_url = "https://demo-api-capital.backend-capital.com/api/v1" if demo else "https://api-capital.backend-capital.com/api/v1"
        self.session_token = None
        self.cst_token = None
        self.headers = {
            "X-CAP-API-KEY": self.api_key,
            "Content-Type": "application/json"
        }

    def login(self):
        """Authenticates and retrieves CST and Security tokens."""
        url = f"{self.base_url}/session"
        payload = {
            "identifier": self.identifier,
            "password": self.password
        }
        
        try:
            response = requests.post(url, json=payload, headers=self.headers)
            if response.status_code == 200:
                self.cst_token = response.headers.get("CST")
                self.session_token = response.headers.get("X-SECURITY-TOKEN")
                
                # Update headers for future requests
                self.headers["CST"] = self.cst_token
                self.headers["X-SECURITY-TOKEN"] = self.session_token
                logging.info("Successfully logged into Capital.com API")
                return True
            else:
                logging.error(f"Failed to login: {response.text}")
                return False
        except Exception as e:
            logging.error(f"Login exception: {str(e)}")
            return False

    def get_market_data(self, epic):
        """Fetches market information for a specific symbol (e.g., 'BTCUSD')."""
        url = f"{self.base_url}/markets/{epic}"
        try:
            response = requests.get(url, headers=self.headers)
            return response.json()
        except Exception as e:
            logging.error(f"Error fetching market data: {str(e)}")
            return None

    def get_prices(self, epic, resolution='MINUTE', count=10):
        """Fetches historical price data."""
        url = f"{self.base_url}/prices/{epic}"
        params = {
            "resolution": resolution,
            "max": count
        }
        try:
            response = requests.get(url, headers=self.headers, params=params)
            return response.json()
        except Exception as e:
            logging.error(f"Error fetching prices: {str(e)}")
            return None

    def get_accounts(self):
        """Fetches account information including balance."""
        url = f"{self.base_url}/accounts"
        try:
            response = requests.get(url, headers=self.headers)
            return response.json()
        except Exception as e:
            logging.error(f"Error fetching accounts: {str(e)}")
            return None

    def get_positions(self):
        """Fetches open positions."""
        url = f"{self.base_url}/positions"
        try:
            response = requests.get(url, headers=self.headers)
            return response.json()
        except Exception as e:
            logging.error(f"Error fetching positions: {str(e)}")
            return None

    def get_trade_history(self, endpoint=None, params=None):
        """Fetches trade history or closed positions from Capital API."""
        if endpoint is None:
            endpoint = os.getenv("CAPITAL_TRADE_HISTORY_ENDPOINT", "/positions/history")

        url = endpoint if endpoint.startswith("http") else f"{self.base_url}{endpoint}"
        try:
            response = requests.get(url, headers=self.headers, params=params)
            if response.status_code == 200:
                return response.json()
            logging.warning(f"Trade history request returned {response.status_code}: {response.text}")
            return None
        except Exception as e:
            logging.error(f"Error fetching trade history from {url}: {str(e)}")
            return None

    def open_position(self, epic, direction, size, stop_level=None, limit_level=None):
        """Opens a new position."""
        url = f"{self.base_url}/positions"
        payload = {
            "epic": epic,
            "direction": direction,  # BUY or SELL
            "size": size,
            "stopLevel": stop_level,
            "limitLevel": limit_level
        }
        try:
            response = requests.post(url, json=payload, headers=self.headers)
            return response.json()
        except Exception as e:
            logging.error(f"Error opening position: {str(e)}")
            return None

    def close_position(self, deal_id):
        """Closes an open position."""
        url = f"{self.base_url}/positions/{deal_id}"
        try:
            response = requests.delete(url, headers=self.headers)
            return response.json()
        except Exception as e:
            logging.error(f"Error closing position: {str(e)}")
            return None
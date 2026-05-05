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
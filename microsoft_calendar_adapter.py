# microsoft_calendar_adapter.py

import os
import json
import requests
from pathlib import Path
from datetime import datetime, timedelta

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

TOKENS_DIR = Path("tokens")
TOKENS_DIR.mkdir(exist_ok=True)


class MicrosoftCalendarAdapter:
    def __init__(self, user_id: str):
        self.user_id = user_id

        self.client_id = os.getenv("MICROSOFT_CLIENT_ID")
        self.client_secret = os.getenv("MICROSOFT_CLIENT_SECRET")
        self.tenant_id = os.getenv("MICROSOFT_TENANT_ID")
        self.redirect_uri = os.getenv("MICROSOFT_REDIRECT_URI")

        self.token_path = TOKENS_DIR / f"{user_id}.json"
        self.token_data = self._load_token()

    def _load_token(self):
        if self.token_path.exists():
            with open(self.token_path, "r") as f:
                return json.load(f)
        return None

    def _save_token(self, token_data):
        with open(self.token_path, "w") as f:
            json.dump(token_data, f)

    def is_connected(self) -> bool:
        return self.token_data is not None

    def _refresh_token_if_needed(self):
        if not self.token_data:
            raise Exception("No token available")

        expires_at = datetime.fromisoformat(self.token_data["expires_at"])
        if datetime.utcnow() < expires_at - timedelta(minutes=2):
            return

        token_url = TOKEN_URL_TEMPLATE.format(tenant=self.tenant_id)

        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.token_data["refresh_token"],
            "redirect_uri": self.redirect_uri,
        }

        response = requests.post(token_url, data=data)
        response.raise_for_status()
        new_token = response.json()

        new_token_data = {
            "access_token": new_token["access_token"],
            "refresh_token": new_token.get("refresh_token", self.token_data["refresh_token"]),
            "expires_at": (
                datetime.utcnow() + timedelta(seconds=new_token["expires_in"])
            ).isoformat(),
        }

        self.token_data = new_token_data
        self._save_token(new_token_data)

    def _get_headers(self):
        self._refresh_token_if_needed()
        return {
            "Authorization": f"Bearer {self.token_data['access_token']}",
            "Content-Type": "application/json",
        }

    def get_events(self):
        headers = self._get_headers()
        response = requests.get(f"{GRAPH_BASE_URL}/me/events", headers=headers)
        response.raise_for_status()
        return response.json()

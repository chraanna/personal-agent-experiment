# microsoft_calendar_adapter.py

import os
import json
import requests
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
TOKENS_DIR = DATA_DIR / "tokens"
TOKENS_DIR.mkdir(parents=True, exist_ok=True)

# Microsoft → normalized response mapping
_RESPONSE_MAP = {
    "notResponded": "needsAction",
    "none": "needsAction",
    "tentativelyAccepted": "tentative",
    "accepted": "accepted",
    "declined": "declined",
}


class MicrosoftCalendarAdapter:
    def __init__(self, user_id: str):
        self.user_id = user_id

        self.client_id = os.getenv("MICROSOFT_CLIENT_ID")
        self.client_secret = os.getenv("MICROSOFT_CLIENT_SECRET")
        self.tenant_id = os.getenv("MICROSOFT_TENANT_ID")
        self.redirect_uri = os.getenv("MICROSOFT_REDIRECT_URI")

        self.token_path = TOKENS_DIR / f"{user_id}.json"
        self.token_data = self._load_token()
        self._lock = threading.Lock()

    def _load_token(self):
        if self.token_path.exists():
            with open(self.token_path, "r") as f:
                return json.load(f)
        return None

    def _save_token(self, token_data):
        with open(self.token_path, "w") as f:
            json.dump(token_data, f)

    def save_token(self, token_data):
        """Public method for saving tokens from OAuth callback."""
        self.token_data = token_data
        self._save_token(token_data)

    def is_connected(self) -> bool:
        return self.token_data is not None

    def _refresh_token_if_needed(self):
        with self._lock:
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
            "Prefer": 'outlook.timezone="UTC"',
        }

    def get_events(self, start_utc: datetime, end_utc: datetime) -> list[dict]:
        headers = self._get_headers()

        start_str = start_utc.strftime("%Y-%m-%dT%H:%M:%S")
        end_str = end_utc.strftime("%Y-%m-%dT%H:%M:%S")

        url = (
            f"{GRAPH_BASE_URL}/me/calendarview"
            f"?startdatetime={start_str}"
            f"&enddatetime={end_str}"
            f"&$orderby=start/dateTime"
            f"&$top=100"
        )

        response = requests.get(url, headers=headers)
        response.raise_for_status()

        events = []
        for item in response.json().get("value", []):
            start_raw = item.get("start", {})
            end_raw = item.get("end", {})

            # All-day events
            if item.get("isAllDay"):
                start_dt = datetime.fromisoformat(start_raw["dateTime"][:10]).replace(tzinfo=timezone.utc)
                end_dt = datetime.fromisoformat(end_raw["dateTime"][:10]).replace(tzinfo=timezone.utc)
                all_day = True
            else:
                start_dt = datetime.fromisoformat(start_raw["dateTime"]).replace(tzinfo=timezone.utc)
                end_dt = datetime.fromisoformat(end_raw["dateTime"]).replace(tzinfo=timezone.utc)
                all_day = False

            # Response status
            ms_response = item.get("responseStatus", {}).get("response", "none")
            response_normalized = _RESPONSE_MAP.get(ms_response, "needsAction")

            # Organizer
            organizer = item.get("organizer", {}).get("emailAddress", {})
            organizer_email = organizer.get("address")

            events.append({
                "id": item["id"],
                "summary": item.get("subject", "Möte"),
                "start": start_dt,
                "end": end_dt,
                "all_day": all_day,
                "response": response_normalized,
                "organizer_email": organizer_email,
            })

        return events

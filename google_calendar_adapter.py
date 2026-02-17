# google_calendar_adapter.py

import os
import json
import requests
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone

GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
TOKENS_DIR = DATA_DIR / "tokens"
TOKENS_DIR.mkdir(parents=True, exist_ok=True)

# Google → normalized response mapping
_RESPONSE_MAP = {
    "needsAction": "needsAction",
    "tentative": "tentative",
    "accepted": "accepted",
    "declined": "declined",
}


class GoogleCalendarAdapter:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.provider = "google"

        self.client_id = os.getenv("GOOGLE_CLIENT_ID")
        self.client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        self.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")

        self.token_path = TOKENS_DIR / f"{user_id}.json"
        self.token_data = self._load_token()
        self._lock = threading.Lock()

    def _load_token(self):
        if self.token_path.exists():
            with open(self.token_path, "r") as f:
                data = json.load(f)
            if data.get("provider") == "google":
                return data
        return None

    def _save_token(self, token_data):
        with open(self.token_path, "w") as f:
            json.dump(token_data, f)

    def save_token(self, token_data):
        """Public method for saving tokens from OAuth callback."""
        token_data["provider"] = "google"
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

            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.token_data["refresh_token"],
            }

            response = requests.post(GOOGLE_TOKEN_URL, data=data)
            response.raise_for_status()
            new_token = response.json()

            new_token_data = {
                "provider": "google",
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
        }

    def get_events(self, start_utc: datetime, end_utc: datetime) -> list[dict]:
        headers = self._get_headers()

        start_str = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        url = (
            f"{GOOGLE_CALENDAR_API}/calendars/primary/events"
            f"?timeMin={start_str}"
            f"&timeMax={end_str}"
            f"&singleEvents=true"
            f"&orderBy=startTime"
            f"&maxResults=100"
        )

        response = requests.get(url, headers=headers)
        response.raise_for_status()

        events = []
        for item in response.json().get("items", []):
            start_raw = item.get("start", {})
            end_raw = item.get("end", {})

            # All-day events use "date", timed events use "dateTime"
            if "date" in start_raw:
                start_dt = datetime.fromisoformat(start_raw["date"]).replace(tzinfo=timezone.utc)
                end_dt = datetime.fromisoformat(end_raw["date"]).replace(tzinfo=timezone.utc)
                all_day = True
            else:
                start_dt = datetime.fromisoformat(
                    start_raw["dateTime"].replace("Z", "+00:00")
                )
                end_dt = datetime.fromisoformat(
                    end_raw["dateTime"].replace("Z", "+00:00")
                )
                # Normalize to UTC
                start_dt = start_dt.astimezone(timezone.utc)
                end_dt = end_dt.astimezone(timezone.utc)
                all_day = False

            # Response status
            attendees = item.get("attendees", [])
            response_status = "accepted"  # default for events you created
            for att in attendees:
                if att.get("self"):
                    response_status = _RESPONSE_MAP.get(
                        att.get("responseStatus", "needsAction"), "needsAction"
                    )
                    break

            organizer_email = item.get("organizer", {}).get("email")

            events.append({
                "id": item["id"],
                "summary": item.get("summary", "Möte"),
                "start": start_dt,
                "end": end_dt,
                "all_day": all_day,
                "response": response_status,
                "organizer_email": organizer_email,
            })

        return events

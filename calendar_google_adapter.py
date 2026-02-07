from datetime import datetime, timezone
from typing import List, Tuple
import json
import os

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

# --------------------------------------------------
# Types
# --------------------------------------------------

BusyBlock = Tuple[datetime, datetime]

# --------------------------------------------------
# Config
# --------------------------------------------------

TOKEN_FILE = "token.json"

# --------------------------------------------------
# Credentials
# --------------------------------------------------

def load_credentials() -> Credentials:
    if not os.path.exists(TOKEN_FILE):
        raise RuntimeError("token.json saknas")

    with open(TOKEN_FILE, "r") as f:
        data = json.load(f)

    return Credentials(
        token=data["token"],
        refresh_token=data.get("refresh_token"),
        token_uri=data["token_uri"],
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        scopes=data["scopes"],
    )

# --------------------------------------------------
# Google Calendar Adapter
# --------------------------------------------------

class GoogleCalendarAdapter:
    def __init__(self):
        creds = load_credentials()
        self.service = build("calendar", "v3", credentials=creds)

    def list_calendars(self) -> List[str]:
        """Return all calendar IDs the user has access to."""
        calendars = []
        page_token = None

        while True:
            result = self.service.calendarList().list(
                pageToken=page_token
            ).execute()

            for item in result.get("items", []):
                calendars.append(item["id"])

            page_token = result.get("nextPageToken")
            if not page_token:
                break

        return calendars

    def get_busy_blocks(
        self,
        start_utc: datetime,
        end_utc: datetime,
    ) -> List[BusyBlock]:
        """
        Returns a unified list of busy blocks across ALL calendars.
        """
        busy: List[BusyBlock] = []
        calendar_ids = self.list_calendars()

        for calendar_id in calendar_ids:
            events = self.service.events().list(
                calendarId=calendar_id,
                timeMin=start_utc.isoformat(),
                timeMax=end_utc.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            ).execute().get("items", [])

            for event in events:
                start_raw = event["start"].get("dateTime")
                end_raw = event["end"].get("dateTime")

                # Skip all-day events for now
                if not start_raw or not end_raw:
                    continue

                start = datetime.fromisoformat(
                    start_raw.replace("Z", "+00:00")
                ).astimezone(timezone.utc)

                end = datetime.fromisoformat(
                    end_raw.replace("Z", "+00:00")
                ).astimezone(timezone.utc)

                busy.append((start, end))

        return busy

from datetime import datetime, timezone
from typing import List, Dict
import json
import os

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

CalendarEvent = Dict[str, object]

TOKEN_FILE = "token.json"


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


def parse_rfc3339(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def to_rfc3339_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class GoogleCalendarAdapter:
    def __init__(self):
        creds = load_credentials()
        self.service = build("calendar", "v3", credentials=creds)

    def get_events(self, start_utc: datetime, end_utc: datetime) -> List[CalendarEvent]:
        result = self.service.events().list(
            calendarId="primary",
            timeMin=to_rfc3339_utc(start_utc),
            timeMax=to_rfc3339_utc(end_utc),
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events: List[CalendarEvent] = []

        for event in result.get("items", []):
            # --------
            # Tid
            # --------
            start_dt = event["start"].get("dateTime")
            end_dt = event["end"].get("dateTime")

            all_day = False
            if not start_dt or not end_dt:
                # Heldagsevent använder date istället för dateTime
                start_date = event["start"].get("date")
                end_date = event["end"].get("date")
                if not start_date or not end_date:
                    continue

                all_day = True
                start = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
                end = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
            else:
                start = parse_rfc3339(start_dt)
                end = parse_rfc3339(end_dt)

            # --------
            # Svarstatus
            # --------
            response = "unknown"
            for attendee in event.get("attendees", []):
                if attendee.get("self"):
                    response = attendee.get("responseStatus", "unknown")
                    break

            # --------
            # Vem skapade mötet
            # --------
            organizer = event.get("organizer", {})
            organizer_email = organizer.get("email")

            events.append(
                {
                    "id": event["id"],
                    "summary": event.get("summary", "Möte"),
                    "start": start,
                    "end": end,
                    "all_day": all_day,
                    "response": response,  # accepted / needsAction / declined
                    "organizer_email": organizer_email,
                }
            )

        return events

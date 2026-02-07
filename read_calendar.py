from datetime import datetime, timedelta, timezone
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import json
import os

# --------------------------------------------------
# Configuration
# --------------------------------------------------

TOKEN_FILE = "token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

# --------------------------------------------------
# Helpers
# --------------------------------------------------

def load_credentials():
    if not os.path.exists(TOKEN_FILE):
        raise RuntimeError("token.json saknas. Kör auth-flödet först.")

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


def format_time(iso_str):
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    creds = load_credentials()
    service = build("calendar", "v3", credentials=creds)

    now = datetime.now(timezone.utc)
    end = now + timedelta(days=7)

    events_result = service.events().list(
        calendarId="primary",
        timeMin=now.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = events_result.get("items", [])

    print("\n📅 Kalender – kommande 7 dagar\n")

    if not events:
        print("Inga events hittades.")
        return

    for event in events:
        start = event["start"].get("dateTime") or event["start"].get("date")
        end = event["end"].get("dateTime") or event["end"].get("date")

        print(f"- {format_time(start)} → {format_time(end)}")
        print(f"  {event.get('summary', '(ingen titel)')}\n")


if __name__ == "__main__":
    main()

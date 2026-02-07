from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import os
import json

# --------------------------------------------------
# Configuration
# --------------------------------------------------

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "token.json"
REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"

# --------------------------------------------------
# Auth + Calendar service
# --------------------------------------------------

def get_calendar_service():
    creds = None

    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as token:
            data = json.load(token)

        creds = Credentials(
            token=data["token"],
            refresh_token=data.get("refresh_token"),
            token_uri=data["token_uri"],
            client_id=data["client_id"],
            client_secret=data["client_secret"],
            scopes=data["scopes"],
        )

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            CLIENT_SECRET_FILE,
            SCOPES,
            redirect_uri=REDIRECT_URI,
        )

        auth_url, _ = flow.authorization_url(
            prompt="consent",
            access_type="offline",
        )

        print("\nÖppna denna URL i din webbläsare:\n")
        print(auth_url)

        code = input("\nKlistra in koden här: ").strip()

        flow.fetch_token(code=code)
        creds = flow.credentials

        with open(TOKEN_FILE, "w") as token:
            json.dump(
                {
                    "token": creds.token,
                    "refresh_token": creds.refresh_token,
                    "token_uri": creds.token_uri,
                    "client_id": creds.client_id,
                    "client_secret": creds.client_secret,
                    "scopes": creds.scopes,
                },
                token,
            )

    return build("calendar", "v3", credentials=creds)

# --------------------------------------------------
# Manual test
# --------------------------------------------------

if __name__ == "__main__":
    service = get_calendar_service()

    calendars = service.calendarList().list().execute().get("items", [])
    print("\nKalendrar:")
    for cal in calendars:
        print(f"- {cal.get('summary')}")

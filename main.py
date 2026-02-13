import os
import requests
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# =========================
# ENV
# =========================

CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID")
CLIENT_SECRET = os.getenv("MICROSOFT_CLIENT_SECRET")
TENANT_ID = os.getenv("MICROSOFT_TENANT_ID")
REDIRECT_URI = os.getenv("MICROSOFT_REDIRECT_URI")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
AUTHORIZE_URL = f"{AUTHORITY}/oauth2/v2.0/authorize"
TOKEN_URL = f"{AUTHORITY}/oauth2/v2.0/token"

SCOPES = [
    "https://graph.microsoft.com/Calendars.Read",
    "offline_access"
]

# Dev only – in memory
user_tokens = {}

# =========================
# AUTH
# =========================

@app.get("/auth/login")
def login():
    scope_str = " ".join(SCOPES)

    url = (
        f"{AUTHORIZE_URL}"
        f"?client_id={CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_mode=query"
        f"&scope={scope_str}"
        f"&state=shilpi"
    )

    # IMPORTANT:
    # Return the login URL instead of redirecting (Codespaces proxy workaround)
    return {"login_url": url}


@app.get("/auth/callback")
def callback(request: Request):
    code = request.query_params.get("code")

    if not code:
        return JSONResponse({"error": "No code returned"})

    token_data = {
        "client_id": CLIENT_ID,
        "scope": " ".join(SCOPES),
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
        "client_secret": CLIENT_SECRET,
    }

    response = requests.post(TOKEN_URL, data=token_data)

    if response.status_code != 200:
        return JSONResponse({
            "error": "Token exchange failed",
            "details": response.text
        })

    token_json = response.json()
    access_token = token_json.get("access_token")

    if not access_token:
        return JSONResponse({"error": "No access token received"})

    user_tokens["shilpi"] = access_token

    return {"status": "authenticated"}


# =========================
# CALENDAR LOGIC
# =========================

def get_calendar_events():
    access_token = user_tokens.get("shilpi")

    if not access_token:
        return None

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    graph_url = (
        "https://graph.microsoft.com/v1.0/me/events"
        "?$orderby=start/dateTime"
        "&$top=20"
    )

    response = requests.get(graph_url, headers=headers)

    if response.status_code != 200:
        return None

    return response.json().get("value", [])


def find_free_slots(events):
    if not events:
        return ["Hela dagen är fri"]

    parsed_events = []

    for event in events:
        start = datetime.fromisoformat(event["start"]["dateTime"])
        end = datetime.fromisoformat(event["end"]["dateTime"])
        parsed_events.append((start, end))

    parsed_events.sort(key=lambda x: x[0])

    free_slots = []

    for i in range(len(parsed_events) - 1):
        current_end = parsed_events[i][1]
        next_start = parsed_events[i + 1][0]

        diff_minutes = (next_start - current_end).total_seconds() / 60

        if diff_minutes >= 30:
            free_slots.append(
                f"{current_end.strftime('%H:%M')} – {next_start.strftime('%H:%M')}"
            )

    if not free_slots:
        return ["Inga större luckor"]

    return free_slots


# =========================
# SHILPI CALENDAR
# =========================

@app.get("/shilpi/calendar")
def shilpi_calendar():

    events = get_calendar_events()

    if events is None:
        return {
            "message": "Du är inte inloggad.",
            "login_url": "/auth/login"
        }

    free_slots = find_free_slots(events)

    return {
        "free_slots": free_slots
    }


# =========================
# ROOT
# =========================

@app.get("/")
def root():
    return {
        "status": "Shilpi is running",
        "login": "/auth/login",
        "check_calendar": "/shilpi/calendar"
    }

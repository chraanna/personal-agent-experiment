import os
import uuid
import threading
import time
import re
import requests

from fastapi import FastAPI, Request, Cookie
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pathlib import Path
from datetime import datetime, timedelta, time as dtime, timezone
from typing import Optional
from dotenv import load_dotenv

from microsoft_calendar_adapter import MicrosoftCalendarAdapter
from find_slots import find_slots_for_day, DAYS_AHEAD

load_dotenv()

app = FastAPI()

# ==================================================
# Microsoft OAuth config
# ==================================================

CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID")
CLIENT_SECRET = os.getenv("MICROSOFT_CLIENT_SECRET")
TENANT_ID = os.getenv("MICROSOFT_TENANT_ID")
REDIRECT_URI = os.getenv("MICROSOFT_REDIRECT_URI")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
AUTHORIZE_URL = f"{AUTHORITY}/oauth2/v2.0/authorize"
TOKEN_URL = f"{AUTHORITY}/oauth2/v2.0/token"

SCOPES = [
    "https://graph.microsoft.com/Calendars.Read",
    "offline_access",
]

# ==================================================
# Copy
# ==================================================

DEFAULT_REPLY = (
    "Jag tar ansvar för sådant du inte ska behöva lägga tid på "
    "som att påminna dig om att ringa mamma, hitta luckor i din kalender "
    "och meddela dig om möten krockar.\n"
    "Jag är redo för nästa uppgift."
)

# ==================================================
# Per-user state
# ==================================================

user_adapters: dict[str, MicrosoftCalendarAdapter] = {}
user_reminders: dict[str, list] = {}
user_reminder_state: dict[str, dict] = {}
user_events: dict[str, list[str]] = {}

# Per-user watcher state
user_calendar_snapshots: dict[str, dict] = {}
user_reported_conflicts: dict[str, set] = {}


def get_adapter(user_id: str) -> Optional[MicrosoftCalendarAdapter]:
    if user_id in user_adapters:
        return user_adapters[user_id]
    token_path = Path("tokens") / f"{user_id}.json"
    if token_path.exists():
        adapter = MicrosoftCalendarAdapter(user_id)
        user_adapters[user_id] = adapter
        return adapter
    return None


def get_reminders(user_id: str) -> list:
    if user_id not in user_reminders:
        user_reminders[user_id] = []
    return user_reminders[user_id]


def get_reminder_state(user_id: str) -> dict:
    if user_id not in user_reminder_state:
        user_reminder_state[user_id] = {"waiting_for_time": False, "task": None}
    return user_reminder_state[user_id]


def push_event(user_id: str, text: str):
    if user_id not in user_events:
        user_events[user_id] = []
    user_events[user_id].append(text)


def get_user_id(request: Request) -> str:
    return request.cookies.get("shilpi_user_id", "")


# ==================================================
# Reminder constants
# ==================================================

WEEKDAYS = {
    "måndag": 0, "tisdag": 1, "onsdag": 2,
    "torsdag": 3, "fredag": 4, "lördag": 5, "söndag": 6,
}

WEEKDAY_NAMES = [
    "måndag", "tisdag", "onsdag",
    "torsdag", "fredag", "lördag", "söndag",
]

STOP_WORDS = ["klar", "ok", "tack", "fixat", "gjort", "klart"]

# ==================================================
# Intent detection
# ==================================================

def is_calendar_question(text: str):
    text = text.lower()
    if "?" in text:
        return True
    keywords = ["vad", "när", "visa", "luckor", "kalender", "möte", "ledig"]
    return any(k in text for k in keywords)


def is_task_like(text: str):
    text = text.lower()
    if is_calendar_question(text):
        return False
    if any(text.startswith(q) for q in ["vad", "när", "visa", "hur"]):
        return False
    if len(text.split()) <= 5:
        return True
    return False


def clean_task(text: str):
    text = text.lower()
    text = re.sub(r"påminn mig att", "", text)
    text = re.sub(r"påminn mig om att", "", text)
    text = re.sub(r"påminn", "", text)
    return text.strip()

# ==================================================
# Time parsing
# ==================================================

def parse_time_expression(text: str):
    text = text.lower()
    now = datetime.now()

    again_match = re.search(r"om (\d+)\s*min", text)
    if again_match:
        minutes = int(again_match.group(1))
        return now + timedelta(minutes=minutes)

    hm_match = re.search(r"(\d{1,2}):(\d{2})", text)
    hour = None
    minute = 0

    if hm_match:
        hour = int(hm_match.group(1))
        minute = int(hm_match.group(2))
    else:
        h_match = re.search(r"\b(\d{1,2})\b", text)
        if h_match:
            hour = int(h_match.group(1))

    if hour is None:
        return None

    if "idag" in text:
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if due < now:
            return None
        return due

    if "imorgon" in text or "i morgon" in text:
        due = (now + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        return due

    for day_name, weekday_target in WEEKDAYS.items():
        if day_name in text:
            days_ahead = weekday_target - now.weekday()
            if "nästa" in text:
                days_ahead += 7
            if days_ahead < 0:
                days_ahead += 7

            due = now + timedelta(days=days_ahead)
            return due.replace(hour=hour, minute=minute, second=0, microsecond=0)

    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due < now:
        return None
    return due

# ==================================================
# Reminder processor (per-user)
# ==================================================

def process_reminders_for_user(user_id: str):
    now = datetime.now()
    reminders = get_reminders(user_id)

    for reminder in reminders[:]:
        if reminder["status"] == "active" and now >= reminder["due_time"]:
            push_event(user_id, f"Nu är det dags att {reminder['task']}.")
            reminder["status"] = "triggered_once"
            reminder["trigger_time"] = now

        elif reminder["status"] == "triggered_once":
            if now >= reminder["trigger_time"] + timedelta(minutes=15):
                push_event(user_id, f"Jag påminner igen. Det är dags att {reminder['task']}.")
                reminder["status"] = "reminded_twice"
                reminder["second_trigger_time"] = now

        elif reminder["status"] == "reminded_twice":
            if now >= reminder["second_trigger_time"] + timedelta(minutes=15):
                push_event(user_id, "Uppgiften är inte längre aktiv.")
                reminders.remove(reminder)

# ==================================================
# Calendar question handler
# ==================================================

def handle_calendar_question(text: str, adapter: MicrosoftCalendarAdapter) -> str:
    lower = text.lower()
    now = datetime.utcnow()

    if "luckor" in lower or "ledig" in lower:
        end = now + timedelta(days=DAYS_AHEAD)
        try:
            all_events = adapter.get_events(now, end)
        except Exception:
            return "Kunde inte hämta kalendern just nu."

        busy_blocks = [
            (e["start"].astimezone(), e["end"].astimezone())
            for e in all_events
            if not e["all_day"] and e["response"] != "declined"
        ]

        suggestions = []
        for day_offset in range(DAYS_AHEAD):
            day = (datetime.now() + timedelta(days=day_offset)).date()
            day_busy = [(s, e) for s, e in busy_blocks if s.date() == day]
            slots = find_slots_for_day(day, day_busy)

            for start, end in slots:
                weekday = WEEKDAY_NAMES[start.weekday()]
                suggestions.append(
                    f"{weekday} {start.strftime('%H:%M')}–{end.strftime('%H:%M')}"
                )
                if len(suggestions) == 5:
                    break
            if len(suggestions) == 5:
                break

        if not suggestions:
            return "Jag ser inga lediga tider den närmaste veckan."

        lines = ["Här är lediga tider:"]
        for s in suggestions:
            lines.append(f"• {s}")
        return "\n".join(lines)

    if "nästa möte" in lower:
        try:
            end = now + timedelta(days=1)
            events = adapter.get_events(now, end)
        except Exception:
            return "Kunde inte hämta kalendern just nu."

        upcoming = [
            e for e in events
            if not e["all_day"] and e["response"] != "declined"
        ]

        if not upcoming:
            return "Du har inga fler möten idag."

        nxt = upcoming[0]
        return f"Nästa möte: {nxt['summary']} kl {nxt['start'].astimezone().strftime('%H:%M')}"

    # Default: show today's schedule
    try:
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        events = adapter.get_events(today_start, today_end)
    except Exception:
        return "Kunde inte hämta kalendern just nu."

    timed = [e for e in events if not e["all_day"] and e["response"] != "declined"]

    if not timed:
        return "Du har inga möten idag."

    lines = ["Dagens schema:"]
    for e in timed:
        start_local = e["start"].astimezone()
        end_local = e["end"].astimezone()
        lines.append(
            f"• {start_local.strftime('%H:%M')}–{end_local.strftime('%H:%M')} {e['summary']}"
        )
    return "\n".join(lines)

# ==================================================
# Calendar watcher (multi-user)
# ==================================================

POLL_SECONDS = 30
LOOKAHEAD_DAYS = 14


def overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and a_end > b_start


def calendar_watcher():
    while True:
        for user_id, adapter in list(user_adapters.items()):
            if not adapter.is_connected():
                continue

            try:
                now = datetime.utcnow()
                end = now + timedelta(days=LOOKAHEAD_DAYS)

                all_events = adapter.get_events(now, end)
                accepted = [e for e in all_events if e["response"] == "accepted"]
                pending = [e for e in all_events if e["response"] == "needsAction"]

                snapshot = user_calendar_snapshots.get(user_id)
                if snapshot is None:
                    user_calendar_snapshots[user_id] = {e["id"]: e for e in all_events}
                    continue

                if user_id not in user_reported_conflicts:
                    user_reported_conflicts[user_id] = set()

                for p in pending:
                    for a in accepted:
                        if overlaps(p["start"], p["end"], a["start"], a["end"]):
                            key = f"{p['id']}|{a['id']}"
                            if key in user_reported_conflicts[user_id]:
                                continue

                            user_reported_conflicts[user_id].add(key)

                            msg = (
                                "Ny aktivitet i din kalender:\n"
                                f"Du har möte med {a['summary']} kl {a['start'].astimezone().strftime('%H:%M')} "
                                f"den {a['start'].astimezone().strftime('%Y-%m-%d')}.\n"
                                f"Krockar med mötesförfrågan från {p['summary']}"
                            )
                            push_event(user_id, msg)

                user_calendar_snapshots[user_id] = {e["id"]: e for e in all_events}

            except Exception:
                pass

            process_reminders_for_user(user_id)

        # Also process reminders for users without adapters (reminder-only users)
        for user_id in list(user_reminders.keys()):
            if user_id not in user_adapters:
                process_reminders_for_user(user_id)

        time.sleep(POLL_SECONDS)


@app.on_event("startup")
def start_watcher():
    threading.Thread(target=calendar_watcher, daemon=True).start()

# ==================================================
# OAuth routes
# ==================================================

@app.get("/auth/login")
def login():
    scope_str = " ".join(SCOPES)
    state = str(uuid.uuid4())

    url = (
        f"{AUTHORIZE_URL}"
        f"?client_id={CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_mode=query"
        f"&scope={scope_str}"
        f"&state={state}"
    )

    return {"login_url": url, "user_id": state}


@app.get("/auth/callback")
def callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state", "")

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
            "details": response.text,
        })

    token_json = response.json()
    access_token = token_json.get("access_token")

    if not access_token:
        return JSONResponse({"error": "No access token received"})

    user_id = state if state else str(uuid.uuid4())

    token_store = {
        "access_token": access_token,
        "refresh_token": token_json.get("refresh_token", ""),
        "expires_at": (
            datetime.utcnow() + timedelta(seconds=token_json.get("expires_in", 3600))
        ).isoformat(),
    }

    adapter = MicrosoftCalendarAdapter(user_id)
    adapter.save_token(token_store)
    user_adapters[user_id] = adapter

    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie(key="shilpi_user_id", value=user_id, httponly=True, max_age=60*60*24*30)
    return resp


@app.get("/auth/status")
async def auth_status(request: Request):
    user_id = get_user_id(request)
    if not user_id:
        return JSONResponse({"connected": False, "next_meeting": None})

    adapter = get_adapter(user_id)
    if not adapter or not adapter.is_connected():
        return JSONResponse({"connected": False, "next_meeting": None})

    next_meeting = None
    try:
        now = datetime.utcnow()
        end = now + timedelta(days=1)
        events = adapter.get_events(now, end)
        upcoming = [
            e for e in events
            if not e["all_day"] and e["response"] != "declined"
        ]
        if upcoming:
            nxt = upcoming[0]
            next_meeting = f"{nxt['summary']} kl {nxt['start'].astimezone().strftime('%H:%M')}"
    except Exception:
        pass

    return JSONResponse({"connected": True, "next_meeting": next_meeting})

# ==================================================
# Chat endpoint
# ==================================================

@app.post("/chat")
async def chat(payload: dict, request: Request):
    message = payload.get("message", "").strip()
    lower = message.lower()
    user_id = get_user_id(request)

    if not user_id:
        user_id = str(uuid.uuid4())

    reminders = get_reminders(user_id)
    state = get_reminder_state(user_id)

    # manual stop
    if lower in STOP_WORDS:
        if reminders:
            reminders.clear()
            return {"reply": "Uppgiften är inte längre aktiv.", "user_id": user_id}
        return {"reply": DEFAULT_REPLY, "user_id": user_id}

    # påminn igen
    if "påminn igen" in lower:
        if reminders:
            new_time = parse_time_expression(lower)
            if new_time:
                reminders[0]["due_time"] = new_time
                reminders[0]["status"] = "active"
                return {"reply": "Jag påminner dig igen.", "user_id": user_id}

    # waiting for time
    if state["waiting_for_time"]:
        due = parse_time_expression(lower)
        if not due:
            return {"reply": "Tid och dag?", "user_id": user_id}

        task = state["task"]
        state["waiting_for_time"] = False

        reminders.append({
            "task": task,
            "due_time": due,
            "status": "active",
            "trigger_time": None,
            "second_trigger_time": None,
        })

        weekday_name = WEEKDAY_NAMES[due.weekday()]
        return {
            "reply": f"Jag påminner dig att {task} på {weekday_name} kl {due.strftime('%H:%M')}.",
            "user_id": user_id,
        }

    # calendar question
    if is_calendar_question(lower):
        adapter = get_adapter(user_id)
        if adapter and adapter.is_connected():
            reply = handle_calendar_question(message, adapter)
            return {"reply": reply, "user_id": user_id}
        else:
            return {
                "reply": "Du behöver logga in med Microsoft för att jag ska kunna se din kalender.",
                "user_id": user_id,
            }

    # new task
    if "påminn" in lower or is_task_like(lower):
        state["waiting_for_time"] = True
        state["task"] = clean_task(lower)
        return {"reply": "Tid och dag?", "user_id": user_id}

    return {"reply": DEFAULT_REPLY, "user_id": user_id}


@app.get("/events")
async def get_events(request: Request):
    user_id = get_user_id(request)
    if not user_id:
        return JSONResponse([])

    out = user_events.get(user_id, [])[:]
    user_events[user_id] = []
    return JSONResponse(out)


@app.get("/", response_class=HTMLResponse)
def ui():
    return Path("index.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

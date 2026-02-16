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
from zoneinfo import ZoneInfo
from typing import Optional
from dotenv import load_dotenv

LOCAL_TZ = ZoneInfo("Europe/Stockholm")

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
    "mån": 0, "tis": 1, "ons": 2,
    "tors": 3, "fre": 4, "lör": 5, "sön": 6,
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


def clean_task(text: str):
    text = text.lower()
    text = re.sub(r"påminn mig att", "", text)
    text = re.sub(r"påminn mig om att", "", text)
    text = re.sub(r"påminn", "", text)
    return text.strip()

# ==================================================
# Time parsing
# ==================================================

def normalize_input(text: str) -> str:
    """Normalize common Swedish variations before parsing."""
    text = text.lower()
    text = re.sub(r"\bklockan\b", "kl", text)
    # Common misspellings of "imorgon"
    text = re.sub(r"\bimorogn\b", "imorgon", text)
    text = re.sub(r"\bimorrgon\b", "imorgon", text)
    text = re.sub(r"\bimorgn\b", "imorgon", text)
    return text


def parse_time_expression(text: str):
    text = normalize_input(text)
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

    # Only time, no day → return None (caller should ask "Vilken dag?")
    return None


def has_day_reference(text: str) -> bool:
    """Check if text contains a day reference (idag, imorgon, weekday, om X min)."""
    text = normalize_input(text)
    if re.search(r"om \d+\s*min", text):
        return True
    if "idag" in text or "imorgon" in text or "i morgon" in text:
        return True
    for day_name in WEEKDAYS:
        if day_name in text:
            return True
    return False


def parse_multiple_days(text: str) -> list:
    """Parse multiple day references from text. Returns list of dates."""
    text = normalize_input(text)
    now = datetime.now()
    today = now.date()
    dates = []
    found_weekdays = set()

    if "idag" in text:
        dates.append(today)
    if "imorgon" in text or "i morgon" in text:
        dates.append(today + timedelta(days=1))

    # Sort by length descending so "onsdag" matches before "ons"
    for day_name, weekday_target in sorted(WEEKDAYS.items(), key=lambda x: -len(x[0])):
        if day_name in text and weekday_target not in found_weekdays:
            found_weekdays.add(weekday_target)
            days_ahead = weekday_target - now.weekday()
            if "nästa" in text:
                days_ahead += 7
            if days_ahead < 0:
                days_ahead += 7
            dates.append(today + timedelta(days=days_ahead))

    dates.sort()
    return dates


def has_multiple_days(text: str) -> bool:
    """Check if text references more than one day."""
    return len(parse_multiple_days(text)) > 1


def parse_time_only(text: str):
    """Extract just the hour and minute from text, ignoring day references."""
    text = normalize_input(text)
    hm_match = re.search(r"(\d{1,2}):(\d{2})", text)
    if hm_match:
        return int(hm_match.group(1)), int(hm_match.group(2))
    h_match = re.search(r"\bkl\s*(\d{1,2})\b", text)
    if h_match:
        return int(h_match.group(1)), 0
    h_match = re.search(r"\b(\d{1,2})\b", text)
    if h_match:
        val = int(h_match.group(1))
        if 6 <= val <= 23:
            return val, 0
    return None, None


def format_day_label(d, today) -> str:
    """Format a date as weekday name (this week) or d/m (further out)."""
    days_diff = (d - today).days
    if d == today:
        return "idag"
    if d == today + timedelta(days=1):
        return "imorgon"
    if days_diff <= 7:
        return WEEKDAY_NAMES[d.weekday()]
    return f"den {d.day}/{d.month}"


def format_multiple_days(dates: list, hour: int, minute: int) -> str:
    """Format a confirmation for multiple reminder days."""
    today = datetime.now().date()
    labels = [format_day_label(d, today) for d in dates]
    time_str = f"kl {hour:02d}:{minute:02d}"

    if len(labels) == 1:
        prefix = labels[0]
        # Add "på" for weekday names
        if prefix not in ("idag", "imorgon") and not prefix.startswith("den"):
            prefix = f"på {prefix}"
        return f"Jag påminner dig {prefix} {time_str}."

    # Join with commas and "och"
    formatted = []
    for label in labels:
        if label not in ("idag", "imorgon") and not label.startswith("den"):
            formatted.append(f"på {label}")
        else:
            formatted.append(label)

    if len(formatted) == 2:
        day_str = f"{formatted[0]} och {formatted[1]}"
    else:
        day_str = ", ".join(formatted[:-1]) + f" och {formatted[-1]}"

    return f"Jag påminner dig {day_str} {time_str}."


def format_collected_reminders(collected: list) -> str:
    """Format confirmation for collected reminders with different times per day.
    collected = [{"date": date, "hour": int, "minute": int}, ...]
    """
    today = datetime.now().date()
    parts = []
    for item in collected:
        d = item["date"]
        label = format_day_label(d, today)
        if label not in ("idag", "imorgon") and not label.startswith("den"):
            label = f"på {label}"
        parts.append(f"{label} kl {item['hour']:02d}:{item['minute']:02d}")

    if len(parts) == 1:
        return f"Jag påminner dig {parts[0]}."
    if len(parts) == 2:
        return f"Jag påminner dig {parts[0]} och {parts[1]}."
    return f"Jag påminner dig {', '.join(parts[:-1])} och {parts[-1]}."

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

def to_swedish(dt: datetime) -> datetime:
    return dt.astimezone(LOCAL_TZ)


def format_due_time(due: datetime) -> str:
    """Format a reminder time naturally: 'idag kl 14', 'imorgon kl 10:30', 'på måndag kl 09'."""
    now = datetime.now()
    time_str = f"kl {due.strftime('%H:%M')}"

    if due.date() == now.date():
        return f"idag {time_str}"
    if due.date() == (now + timedelta(days=1)).date():
        return f"imorgon {time_str}"

    weekday = WEEKDAY_NAMES[due.weekday()]
    days_diff = (due.date() - now.date()).days
    if days_diff <= 7:
        return f"på {weekday} {time_str}"

    return f"den {due.day}/{due.month} {time_str}"


def parse_date_from_text(text: str) -> tuple:
    """Parse a date reference from text. Returns (date, is_this_week)."""
    lower = text.lower()
    today = datetime.now(LOCAL_TZ)

    # Explicit date: "13/4", "13/04", "3/2"
    date_match = re.search(r"(\d{1,2})/(\d{1,2})", lower)
    if date_match:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        year = today.year
        try:
            target = datetime(year, month, day, tzinfo=LOCAL_TZ).date()
            if target < today.date():
                target = datetime(year + 1, month, day, tzinfo=LOCAL_TZ).date()
            return target, False
        except ValueError:
            pass

    if "idag" in lower:
        return today.date(), True
    if "imorgon" in lower or "i morgon" in lower:
        return (today + timedelta(days=1)).date(), True

    for day_name, weekday_num in WEEKDAYS.items():
        if day_name in lower:
            days_ahead = weekday_num - today.weekday()
            if "nästa" in lower:
                days_ahead += 7
                target = (today + timedelta(days=days_ahead)).date()
                return target, False
            if days_ahead < 0:
                days_ahead += 7
            target = (today + timedelta(days=days_ahead)).date()
            return target, True

    return None, None


def format_event_time(event: dict, use_weekday: bool) -> str:
    """Format an event line. use_weekday=True → 'måndag 10:00', False → '17/2 10:00'."""
    s = to_swedish(event["start"])
    en = to_swedish(event["end"])
    if use_weekday:
        day_label = WEEKDAY_NAMES[s.weekday()]
    else:
        day_label = s.strftime("%-d/%-m")
    return f"• {day_label} {s.strftime('%H:%M')}–{en.strftime('%H:%M')} {event['summary']}"


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
            (to_swedish(e["start"]), to_swedish(e["end"]))
            for e in all_events
            if not e["all_day"] and e["response"] != "declined"
        ]

        # Check if the user asked about a specific day
        today = datetime.now(LOCAL_TZ)
        target_days = []

        if "idag" in lower:
            target_days = [today.date()]
        elif "imorgon" in lower or "i morgon" in lower:
            target_days = [(today + timedelta(days=1)).date()]
        else:
            for day_name, weekday_num in WEEKDAYS.items():
                if day_name in lower:
                    days_ahead = weekday_num - today.weekday()
                    if days_ahead < 0:
                        days_ahead += 7
                    target_days = [(today + timedelta(days=days_ahead)).date()]
                    break

        if not target_days:
            # No specific day → show whole week
            target_days = [(today + timedelta(days=d)).date() for d in range(DAYS_AHEAD)]

        suggestions = []
        for day in target_days:
            day_busy = [(s, e) for s, e in busy_blocks if s.date() == day]
            slots = find_slots_for_day(day, day_busy)

            for start, end in slots:
                weekday = WEEKDAY_NAMES[start.weekday()]
                suggestions.append(
                    f"{weekday} {start.strftime('%H:%M')}–{end.strftime('%H:%M')}"
                )

        if not suggestions:
            if len(target_days) == 1:
                return f"Inga lediga tider på {WEEKDAY_NAMES[target_days[0].weekday()]}."
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
        return f"Nästa möte: {nxt['summary']} kl {to_swedish(nxt['start']).strftime('%H:%M')}"

    # "vecka" / "veckan" → show meetings
    if "vecka" in lower:
        is_next_week = "nästa" in lower
        try:
            if is_next_week:
                today = datetime.now(LOCAL_TZ)
                days_to_monday = 7 - today.weekday()
                week_start = (today + timedelta(days=days_to_monday)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                week_end = week_start + timedelta(days=7)
                fetch_start = week_start.astimezone(timezone.utc)
                fetch_end = week_end.astimezone(timezone.utc)
            else:
                fetch_start = now
                fetch_end = now + timedelta(days=7)
            events = adapter.get_events(fetch_start, fetch_end)
        except Exception:
            return "Kunde inte hämta kalendern just nu."

        timed = [e for e in events if not e["all_day"] and e["response"] != "declined"]

        if not timed:
            return "Du har inga möten den närmaste veckan."

        use_weekday = not is_next_week
        header = "Nästa veckas möten:" if is_next_week else "Veckans möten:"
        lines = [header]
        for e in timed:
            lines.append(format_event_time(e, use_weekday))
        return "\n".join(lines)

    # Check for specific date in text
    target_date, is_this_week = parse_date_from_text(lower)
    if target_date:
        try:
            day_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=LOCAL_TZ)
            day_end = day_start + timedelta(days=1)
            events = adapter.get_events(
                day_start.astimezone(timezone.utc),
                day_end.astimezone(timezone.utc),
            )
        except Exception:
            return "Kunde inte hämta kalendern just nu."

        timed = [e for e in events if not e["all_day"] and e["response"] != "declined"]

        if is_this_week:
            day_label = WEEKDAY_NAMES[target_date.weekday()]
        else:
            day_label = f"{target_date.day}/{target_date.month}"

        if not timed:
            return f"Du har inga möten på {day_label}."

        lines = [f"Möten {day_label}:"]
        for e in timed:
            s = to_swedish(e["start"])
            en = to_swedish(e["end"])
            lines.append(f"• {s.strftime('%H:%M')}–{en.strftime('%H:%M')} {e['summary']}")
        return "\n".join(lines)

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
        s = to_swedish(e["start"])
        en = to_swedish(e["end"])
        lines.append(
            f"• {s.strftime('%H:%M')}–{en.strftime('%H:%M')} {e['summary']}"
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

    # "också" — add to existing reminders for same task
    if "också" in lower and ("påminn" in lower) and reminders:
        task = clean_task(lower)
        days = parse_multiple_days(lower)
        hour, minute = parse_time_only(lower)

        if days and hour is not None:
            for d in days:
                due = datetime(d.year, d.month, d.day, hour, minute)
                reminders.append({
                    "task": task, "due_time": due, "status": "active",
                    "trigger_time": None, "second_trigger_time": None,
                })
            today = datetime.now().date()
            labels = [format_day_label(d, today) for d in days]
            if len(labels) == 1:
                day_str = labels[0]
                if day_str not in ("idag", "imorgon") and not day_str.startswith("den"):
                    day_str = f"på {day_str}"
            else:
                day_str = ", ".join(labels[:-1]) + f" och {labels[-1]}"
            return {
                "reply": f"Jag påminner dig också {day_str} kl {hour:02d}:{minute:02d}.",
                "user_id": user_id,
            }

    # påminn igen
    if "påminn igen" in lower:
        if reminders:
            new_time = parse_time_expression(lower)
            if new_time:
                reminders[0]["due_time"] = new_time
                reminders[0]["status"] = "active"
                return {"reply": "Jag påminner dig igen.", "user_id": user_id}

    # waiting for time/day
    if state["waiting_for_time"]:
        waiting_for = state.get("waiting_for", "time_and_day")
        task = state["task"]

        if waiting_for == "multi_day_times":
            # Collecting day+time pairs one by one
            input_text = normalize_input(lower)
            input_days = parse_multiple_days(input_text)
            input_hour, input_minute = parse_time_only(input_text)

            if input_days and input_hour is not None:
                # Got a day+time pair
                collected = state.get("collected", [])
                pending = state.get("pending_days", [])

                for d in input_days:
                    collected.append({"date": d, "hour": input_hour, "minute": input_minute})
                    if d in pending:
                        pending.remove(d)

                state["collected"] = collected
                state["pending_days"] = pending

                if not pending:
                    # All days accounted for → create reminders and confirm
                    for item in collected:
                        d = item["date"]
                        due = datetime(d.year, d.month, d.day, item["hour"], item["minute"])
                        reminders.append({
                            "task": task, "due_time": due, "status": "active",
                            "trigger_time": None, "second_trigger_time": None,
                        })
                    state["waiting_for_time"] = False
                    return {"reply": format_collected_reminders(collected), "user_id": user_id}

                # Still waiting for more days
                return {"reply": "", "user_id": user_id}

            return {"reply": "Skriv dag och tid, t.ex. 'onsdag kl 14'.", "user_id": user_id}

        if waiting_for == "day":
            # We have time, need day(s)
            hour, minute = state.get("pending_hour", 0), state.get("pending_minute", 0)
            days = parse_multiple_days(lower)
            if not days:
                # Try single day via parse_time_expression with combined text
                combined = f"{lower} kl {hour}:{minute:02d}"
                due = parse_time_expression(combined)
                if due:
                    days = [due.date()]
                else:
                    return {"reply": "Vilken dag?", "user_id": user_id}

            for d in days:
                due = datetime(d.year, d.month, d.day, hour, minute)
                reminders.append({
                    "task": task, "due_time": due, "status": "active",
                    "trigger_time": None, "second_trigger_time": None,
                })

            state["waiting_for_time"] = False
            return {"reply": format_multiple_days(days, hour, minute), "user_id": user_id}

        if waiting_for == "time":
            # We have day(s), need time
            hour, minute = parse_time_only(lower)
            if hour is None:
                return {"reply": "Vilken tid?", "user_id": user_id}

            days = state.get("pending_days", [])
            for d in days:
                due = datetime(d.year, d.month, d.day, hour, minute)
                reminders.append({
                    "task": task, "due_time": due, "status": "active",
                    "trigger_time": None, "second_trigger_time": None,
                })

            state["waiting_for_time"] = False
            return {"reply": format_multiple_days(days, hour, minute), "user_id": user_id}

        # waiting for both time and day
        due = parse_time_expression(lower)
        if due:
            reminders.append({
                "task": task, "due_time": due, "status": "active",
                "trigger_time": None, "second_trigger_time": None,
            })
            state["waiting_for_time"] = False
            return {"reply": f"Jag påminner dig {format_due_time(due)}.", "user_id": user_id}

        return {"reply": "Tid och dag?", "user_id": user_id}

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
    if "påminn" in lower:
        task = clean_task(lower)
        days = parse_multiple_days(lower)
        hour, minute = parse_time_only(lower)

        if days and hour is not None:
            # Everything provided → confirm directly
            for d in days:
                due = datetime(d.year, d.month, d.day, hour, minute)
                reminders.append({
                    "task": task, "due_time": due, "status": "active",
                    "trigger_time": None, "second_trigger_time": None,
                })
            return {"reply": format_multiple_days(days, hour, minute), "user_id": user_id}

        if days and hour is None:
            if len(days) > 1:
                # Multiple days, no time → collecting mode
                state["waiting_for_time"] = True
                state["task"] = task
                state["waiting_for"] = "multi_day_times"
                state["pending_days"] = days
                state["collected"] = []
                return {"reply": "Jag kan ta flera påminnelser, men behöver tydligare uppdelning.\nSkriv dag och tid för varje tillfälle.", "user_id": user_id}
            else:
                # Single day, no time → ask for time
                state["waiting_for_time"] = True
                state["task"] = task
                state["waiting_for"] = "time"
                state["pending_days"] = days
                return {"reply": "Vilken tid?", "user_id": user_id}

        if not days and hour is not None:
            # Time but no day → ask for day
            state["waiting_for_time"] = True
            state["task"] = task
            state["waiting_for"] = "day"
            state["pending_hour"] = hour
            state["pending_minute"] = minute
            return {"reply": "Vilken dag?", "user_id": user_id}

        # Nothing → ask for both
        # But first try parse_time_expression for "om 5 min" style
        due = parse_time_expression(lower)
        if due:
            reminders.append({
                "task": task, "due_time": due, "status": "active",
                "trigger_time": None, "second_trigger_time": None,
            })
            return {"reply": f"Jag påminner dig {format_due_time(due)}.", "user_id": user_id}

        state["waiting_for_time"] = True
        state["task"] = task
        state["waiting_for"] = "time_and_day"
        return {"reply": "Tid och dag?", "user_id": user_id}

    # If there are recent reminders and the message looks like a day+time,
    # add as another reminder for the same task
    if reminders:
        days = parse_multiple_days(lower)
        hour, minute = parse_time_only(lower)
        if days and hour is not None:
            last_task = reminders[-1]["task"]
            for d in days:
                due = datetime(d.year, d.month, d.day, hour, minute)
                reminders.append({
                    "task": last_task, "due_time": due, "status": "active",
                    "trigger_time": None, "second_trigger_time": None,
                })
            today = datetime.now().date()
            labels = [format_day_label(d, today) for d in days]
            if len(labels) == 1:
                day_str = labels[0]
                if day_str not in ("idag", "imorgon") and not day_str.startswith("den"):
                    day_str = f"på {day_str}"
            else:
                day_str = ", ".join(labels[:-1]) + f" och {labels[-1]}"
            return {
                "reply": f"Jag påminner dig också {day_str} kl {hour:02d}:{minute:02d}.",
                "user_id": user_id,
            }

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

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path
from datetime import datetime, timedelta, time as dtime
import threading
import time
import re

from calendar_adapter import GoogleCalendarAdapter

app = FastAPI()

# ==================================================
# Copy
# ==================================================

DEFAULT_REPLY = (
    "Jag tar ansvar för sådant du inte ska behöva lägga tid på "
    "som att påminna dig om att ringa mamma, hitta luckor i din kalender "
    "och meddela dig om möten krockar.\n"
    "Jag är redo för nästa uppgift."
)

events: list[str] = []

def push_event(text: str):
    events.append(text)

# ==================================================
# Reminder STATE
# ==================================================

active_reminders = []

reminder_state = {
    "waiting_for_time": False,
    "task": None,
}

WEEKDAYS = {
    "måndag": 0,
    "tisdag": 1,
    "onsdag": 2,
    "torsdag": 3,
    "fredag": 4,
    "lördag": 5,
    "söndag": 6,
}

WEEKDAY_NAMES = [
    "måndag", "tisdag", "onsdag",
    "torsdag", "fredag", "lördag", "söndag"
]

STOP_WORDS = ["klar", "ok", "tack", "fixat", "gjort", "klart"]

# ==================================================
# Intent detection
# ==================================================

def is_calendar_question(text: str):
    text = text.lower()
    if "?" in text:
        return True
    keywords = ["vad", "när", "visa", "luckor", "kalender", "möte"]
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

    # påminn igen om X min
    again_match = re.search(r"om (\d+)\s*min", text)
    if again_match:
        minutes = int(again_match.group(1))
        return now + timedelta(minutes=minutes)

    # HH:MM
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

    # idag
    if "idag" in text:
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if due < now:
            return None
        return due

    # imorgon
    if "imorgon" in text or "i morgon" in text:
        due = (now + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        return due

    # veckodag
    for day_name, weekday_target in WEEKDAYS.items():
        if day_name in text:
            days_ahead = weekday_target - now.weekday()
            if "nästa" in text:
                days_ahead += 7
            if days_ahead < 0:
                days_ahead += 7

            due = now + timedelta(days=days_ahead)
            return due.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # default = idag
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due < now:
        return None
    return due

# ==================================================
# Reminder processor
# ==================================================

def process_reminders():
    now = datetime.now()

    for reminder in active_reminders[:]:
        if reminder["status"] == "active" and now >= reminder["due_time"]:
            push_event(f"Nu är det dags att {reminder['task']}.")
            reminder["status"] = "triggered_once"
            reminder["trigger_time"] = now

        elif reminder["status"] == "triggered_once":
            if now >= reminder["trigger_time"] + timedelta(minutes=15):
                push_event(f"Jag påminner igen. Det är dags att {reminder['task']}.")
                reminder["status"] = "reminded_twice"
                reminder["second_trigger_time"] = now

        elif reminder["status"] == "reminded_twice":
            if now >= reminder["second_trigger_time"] + timedelta(minutes=15):
                push_event("Uppgiften är inte längre aktiv.")
                active_reminders.remove(reminder)

# ==================================================
# Kalender
# ==================================================

POLL_SECONDS = 30
LOOKAHEAD_DAYS = 14

calendar_adapter = GoogleCalendarAdapter()
calendar_snapshot = None
reported_conflicts = set()

def overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and a_end > b_start

def calendar_watcher():
    global calendar_snapshot, reported_conflicts

    while True:
        now = datetime.utcnow()
        end = now + timedelta(days=LOOKAHEAD_DAYS)

        all_events = calendar_adapter.get_events(now, end)
        accepted = [e for e in all_events if e["response"] == "accepted"]
        pending = [e for e in all_events if e["response"] == "needsAction"]

        if calendar_snapshot is None:
            calendar_snapshot = {e["id"]: e for e in all_events}
            time.sleep(POLL_SECONDS)
            continue

        for p in pending:
            for a in accepted:
                if overlaps(p["start"], p["end"], a["start"], a["end"]):
                    key = f"{p['id']}|{a['id']}"
                    if key in reported_conflicts:
                        continue

                    reported_conflicts.add(key)

                    msg = (
                        "Ny aktivitet i din kalender:\n"
                        f"Du har möte med {a['summary']} kl {a['start'].strftime('%H:%M')} "
                        f"den {a['start'].strftime('%Y-%m-%d')}.\n"
                        f"Krockar med mötesförfrågan från {p['summary']}"
                    )

                    push_event(msg)

        calendar_snapshot = {e["id"]: e for e in all_events}

        process_reminders()

        time.sleep(POLL_SECONDS)

@app.on_event("startup")
def start_watcher():
    threading.Thread(target=calendar_watcher, daemon=True).start()

# ==================================================
# Chat endpoint
# ==================================================

@app.post("/chat")
async def chat(payload: dict):
    message = payload.get("message", "").strip()
    lower = message.lower()

    # manual stop
    if lower in STOP_WORDS:
        if active_reminders:
            active_reminders.clear()
            return {"reply": "Uppgiften är inte längre aktiv."}
        return {"reply": DEFAULT_REPLY}

    # påminn igen
    if "påminn igen" in lower:
        if active_reminders:
            new_time = parse_time_expression(lower)
            if new_time:
                active_reminders[0]["due_time"] = new_time
                active_reminders[0]["status"] = "active"
                return {"reply": "Jag påminner dig igen."}

    # waiting for time
    if reminder_state["waiting_for_time"]:
        due = parse_time_expression(lower)
        if not due:
            return {"reply": "Tid och dag?"}

        task = reminder_state["task"]
        reminder_state["waiting_for_time"] = False

        active_reminders.append({
            "task": task,
            "due_time": due,
            "status": "active",
            "trigger_time": None,
            "second_trigger_time": None
        })

        weekday_name = WEEKDAY_NAMES[due.weekday()]
        return {
            "reply": f"Jag påminner dig att {task} på {weekday_name} kl {due.strftime('%H:%M')}."
        }

    # new task
    if "påminn" in lower or is_task_like(lower):
        reminder_state["waiting_for_time"] = True
        reminder_state["task"] = clean_task(lower)
        return {"reply": "Tid och dag?"}

    return {"reply": DEFAULT_REPLY}

@app.get("/events")
async def get_events():
    global events
    out = events[:]
    events = []
    return JSONResponse(out)

@app.get("/", response_class=HTMLResponse)
def ui():
    return Path("index.html").read_text(encoding="utf-8")

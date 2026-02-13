from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path
from typing import Dict
from datetime import datetime, timedelta
import threading
import time
import re

app = FastAPI()

# ==================================================
# Global User Store
# ==================================================

users: Dict[str, dict] = {}

POLL_SECONDS = 30

# ==================================================
# Copy
# ==================================================

ASK_NAME = "Vad heter du?"
WELCOME_TEMPLATE = "Hej {name}. Vad vill du att jag tar ansvar för?"

DEFAULT_REPLY = (
    "Jag tar ansvar för sådant du inte ska behöva lägga tid på, "
    "som att påminna dig om att tex ringa mamma, läsa läxor eller boka träningstid. "
    "Jag hittar också luckor i din kalender och meddelar dig om det möten krockar.\n\n"
    "Jag är redo för nästa uppgift."
)

FINISH_WORDS = ["klar", "fixat", "gjort", "klart"]
ACK_WORDS = ["tack", "ok"]

# ==================================================
# Helpers
# ==================================================

def get_or_create_user(user_id: str, name: str | None = None):
    if user_id not in users:
        users[user_id] = {
            "name": name or user_id,
            "reminders": [],
            "reminder_state": {
                "waiting_for_time": False,
                "task": None
            }
        }
    return users[user_id]

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
        if due <= now:
            return None
        return due

    if "imorgon" in text or "i morgon" in text:
        return (now + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )

    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= now:
        return None
    return due

# ==================================================
# Reminder processor
# ==================================================

def process_reminders():
    now = datetime.now()

    for user in users.values():
        for reminder in user["reminders"][:]:

            if reminder["status"] == "active" and now >= reminder["due_time"]:
                reminder["status"] = "triggered_once"
                reminder["trigger_time"] = now
                reminder["events"].append(
                    f"Nu är det dags att {reminder['task']}."
                )

            elif reminder["status"] == "triggered_once":
                if now >= reminder["trigger_time"] + timedelta(minutes=15):
                    reminder["status"] = "reminded_twice"
                    reminder["second_trigger_time"] = now
                    reminder["events"].append(
                        f"Jag påminner igen. Det är dags att {reminder['task']}."
                    )

            elif reminder["status"] == "reminded_twice":
                if now >= reminder["second_trigger_time"] + timedelta(minutes=15):
                    reminder["events"].append(
                        "Uppgiften är inte längre aktiv."
                    )
                    user["reminders"].remove(reminder)

def reminder_loop():
    while True:
        process_reminders()
        time.sleep(POLL_SECONDS)

@app.on_event("startup")
def start_reminder_loop():
    threading.Thread(target=reminder_loop, daemon=True).start()

# ==================================================
# Routes
# ==================================================

@app.get("/", response_class=HTMLResponse)
def ui():
    return Path("index.html").read_text(encoding="utf-8")

@app.post("/chat")
async def chat(request: Request, response: Response):
    payload = await request.json()
    message = payload.get("message", "").strip()

    if not message:
        return {"reply": ""}

    user_id = request.cookies.get("user_id")

    if not user_id:
        name = message.strip()
        if len(name) < 2:
            return {"reply": ASK_NAME}

        user_id = name.lower()
        get_or_create_user(user_id, name=name)

        response.set_cookie(
            key="user_id",
            value=user_id,
            httponly=False
        )

        return {"reply": WELCOME_TEMPLATE.format(name=name)}

    user = get_or_create_user(user_id)
    lower = message.lower()

    # ====== Avslut ======
    if lower in FINISH_WORDS:
        user["reminders"].clear()
        return {"reply": "Uppgiften är inte längre aktiv."}

    # ====== Bekräftelse ======
    if lower in ACK_WORDS:
        return {"reply": "Jag finns här för sådant du inte ska behöva lägga tid på."}

    # ====== Väntar på tid ======
    if user["reminder_state"]["waiting_for_time"]:
        due = parse_time_expression(lower)
        if not due:
            return {"reply": "Tid och dag?"}

        task = user["reminder_state"]["task"]
        user["reminder_state"]["waiting_for_time"] = False

        user["reminders"].append({
            "task": task,
            "due_time": due,
            "status": "active",
            "trigger_time": None,
            "second_trigger_time": None,
            "events": []
        })

        return {
            "reply": f"Jag påminner dig att {task} kl {due.strftime('%H:%M')}."
        }

    # ====== Ny uppgift ======
    if "påminn" in lower or len(lower.split()) <= 5:
        user["reminder_state"]["waiting_for_time"] = True
        user["reminder_state"]["task"] = clean_task(lower)
        return {"reply": "Tid och dag?"}

    return {"reply": DEFAULT_REPLY}

@app.get("/events")
async def get_events(request: Request):
    user_id = request.cookies.get("user_id")
    if not user_id:
        return JSONResponse([])

    user = get_or_create_user(user_id)

    output = []
    for reminder in user["reminders"]:
        output.extend(reminder["events"])
        reminder["events"].clear()

    return JSONResponse(output)

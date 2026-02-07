from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse
from datetime import datetime, timedelta
import time
import uuid
import re
from pathlib import Path

app = FastAPI()

# =========================================
# In-memory store för åtaganden
# =========================================

commitments = {}

# =========================================
# Enkel tidstolkning
# =========================================

def parse_time_from_message(message: str):
    now = datetime.utcnow()

    match = re.search(r"imorgon kl (\d{1,2})", message.lower())
    if match:
        hour = int(match.group(1))
        remind_at = (now + timedelta(days=1)).replace(
            hour=hour, minute=0, second=0, microsecond=0
        )
        human_text = f"imorgon kl {hour}"
        return remind_at, human_text

    remind_at = now + timedelta(minutes=1)
    return remind_at, "om en minut"

# =========================================
# Skapa åtagande
# =========================================

def create_commitment(text: str, remind_at: datetime):
    commitment_id = str(uuid.uuid4())
    commitments[commitment_id] = {
        "id": commitment_id,
        "text": text,
        "remind_at": remind_at,
        "status": "active"
    }
    return commitments[commitment_id]

# =========================================
# Livscykel
# =========================================

def run_commitment_lifecycle(commitment_id: str):
    commitment = commitments.get(commitment_id)
    if not commitment:
        return

    wait_seconds = (commitment["remind_at"] - datetime.utcnow()).total_seconds()
    if wait_seconds > 0:
        time.sleep(wait_seconds)

    if commitment["status"] != "active":
        return

    print(f"Nu är det dags att {commitment['text']}.")
    commitment["status"] = "delivered"

    time.sleep(30)

    if commitment["status"] != "delivered":
        return

    print(f"Jag påminner igen. Det är dags att {commitment['text']}.")
    commitment["status"] = "reminded"

    commitment["status"] = "inactive"
    print("Uppgiften är inte längre aktiv.")

# =========================================
# Routes
# =========================================

@app.get("/", response_class=HTMLResponse)
def serve_ui():
    html_path = Path("index.html")
    return html_path.read_text(encoding="utf-8")

@app.post("/chat")
async def chat(payload: dict, background_tasks: BackgroundTasks):
    message = payload.get("message", "").strip()
    if not message:
        return {"reply": ""}

    remind_at, human_time = parse_time_from_message(message)

    commitment = create_commitment(
        text=message,
        remind_at=remind_at
    )

    background_tasks.add_task(
        run_commitment_lifecycle,
        commitment["id"]
    )

    return {
        "reply": f"Jag påminner dig {human_time}."
    }
    

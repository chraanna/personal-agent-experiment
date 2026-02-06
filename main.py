from fastapi import FastAPI, Request
from supabase import create_client
from dotenv import load_dotenv
from agent_logic import generate_agent_message
import os

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "verify-me")

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY
)

app = FastAPI()


@app.get("/")
def health_check():
    return {"status": "agent is alive"}


# 🔐 WhatsApp webhook verification
@app.get("/webhook/whatsapp")
def verify_webhook(request: Request):
    params = request.query_params

    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return int(challenge)

    return {"error": "Verification failed"}


# 📩 (kommer senare) inkommande WhatsApp-meddelanden
@app.post("/webhook/whatsapp")
async def receive_whatsapp_message(request: Request):
    payload = await request.json()
    print("Incoming WhatsApp payload:", payload)
    return {"status": "received"}

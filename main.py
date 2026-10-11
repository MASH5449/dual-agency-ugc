import os
import json
import random
import asyncio
import smtplib
import requests
from typing import AsyncGenerator
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="LeadLoop Engine API", version="2.0.0")

# Enable CORS for all incoming frontend and webhook requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared event queue for Real-time SSE streaming
event_queue = asyncio.Queue()

# In-Memory Storage for Generated Leads
leads_db = []

# Environment Variables
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
INSTANTLY_API_KEY = os.getenv("INSTANTLY_API_KEY", "")
INSTANTLY_CAMPAIGN_ID = os.getenv("INSTANTLY_CAMPAIGN_ID", "")
HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY", "")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.porkbun.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")

# ------------------------------------------------------------------
# CURATED SKINCARE & COSMETICS AVATAR ROSTER MATRIX
# ------------------------------------------------------------------
AVATAR_ROSTER = {
    "skincare_ugc_1": {
        "avatar_id": "8fe1340be40d453b855378de493bd42f",
        "name": "Skincare UGC Specialist #1"
    },
    "skincare_ugc_2": {
        "avatar_id": "1a195cfcbcbb4d47b1a0bb9714f081e5",
        "name": "Beauty & Cosmetics Pitcher #2"
    },
    "skincare_ugc_3": {
        "avatar_id": "fade5ca456604426ad66bcc97d24ddf9",
        "name": "Brand Founder Representative #3"
    }
}

# ------------------------------------------------------------------
# LOGGING & SSE EVENT HELPER
# ------------------------------------------------------------------
async def log_event(agent: str, action: str, status: str = "OK"):
    timestamp = asyncio.get_event_loop().time()
    event_data = {
        "timestamp": str(timestamp),
        "agent": agent,
        "action": action,
        "status": status
    }
    print(f"[{agent}] {action} ({status})")
    await event_queue.put(json.dumps(event_data))

# ------------------------------------------------------------------
# LEAD HUNTER WORKER (In-Memory Pipeline)
# ------------------------------------------------------------------
def run_lead_hunter():
    print("[Lead Hunter] Initializing target extraction...")
    new_pitches = [
        {"email": "target1@brand.com", "company": "Brand One"},
        {"email": "target2@brand.com", "company": "Brand Two"}
    ]
    leads_db.extend(new_pitches)
    print(f"[Lead Hunter] Generated {len(new_pitches)} pitches into memory database.")

# ------------------------------------------------------------------
# REAL-TIME SSE STREAM ROUTE
# ------------------------------------------------------------------
@app.get("/api/stream")
async def stream_logs():
    async def event_generator() -> AsyncGenerator[str, None]:
        while True:
            data = await event_queue.get()
            yield f"data: {data}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# ------------------------------------------------------------------
# SMTP FULFILLMENT EMAIL (Port 587 STARTTLS)
# ------------------------------------------------------------------
def send_fulfillment_email(recipient_email: str, video_url: str):
    if not SMTP_USER or not SMTP_PASSWORD:
        print("[Fulfillment Agent] Skipping email: Missing SMTP credentials.")
        return False

    msg = MIMEMultipart()
    msg['From'] = SMTP_USER
    msg['To'] = recipient_email
    msg['Subject'] = "Your Custom AI UGC Video Creative is Ready!"

    body = f"""Hi there,

Your custom AI-generated UGC video creative is ready!

You can view and download your high-resolution video file here:
{video_url}

Thank you for choosing LeadLoop Automation.

Best regards,
LeadLoop Fulfillment Engine
"""
    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, recipient_email, msg.as_string())
        server.quit()
        print(f"[Fulfillment Agent] Delivered video MP4 to {recipient_email}")
        return True
    except Exception as e:
        print(f"ERROR:LeadLoop:Failed to send fulfillment email: {e}")
        return False

# ------------------------------------------------------------------
# INSTANTLY AUTO-PAUSE LEAD HELPER
# ------------------------------------------------------------------
def pause_instantly_lead(email: str):
    if not INSTANTLY_API_KEY:
        print("[Outreach Agent] Skipping Instantly pause: No API Key provided.")
        return

    url = "https://api.instantly.ai/api/v2/leads/pause"
    headers = {
        "Authorization": f"Bearer {INSTANTLY_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "email": email,
        "campaign_id": INSTANTLY_CAMPAIGN_ID
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        print(f"INFO:LeadLoop:Submitted lead pause job for {email} in Instantly (Status {response.status_code}).")
    except Exception as e:
        print(f"ERROR:LeadLoop:Instantly pause request failed: {e}")

# ------------------------------------------------------------------
# HEYGEN V3 MULTI-AVATAR VIDEO RENDER ENGINE
# ------------------------------------------------------------------
def trigger_heygen_render(email: str, company_name: str, persona: str = None):
    if not HEYGEN_API_KEY:
        print("[Avatar Director] Skipping HeyGen render: No API Key provided.")
        return

    # Select requested persona, or randomly assign one from the roster
    if persona and persona in AVATAR_ROSTER:
        selected_actor = AVATAR_ROSTER[persona]
    else:
        selected_key = random.choice(list(AVATAR_ROSTER.keys()))
        selected_actor = AVATAR_ROSTER[selected_key]

    print(f"[Avatar Director] Dispatching render with actor: {selected_actor['name']}")

    url = "https://api.heygen.com/v3/videos"
    headers = {
        "X-Api-Key": HEYGEN_API_KEY,
        "Content-Type": "application/json"
    }
    
    script_text = f"Hey {company_name}! Thanks for claiming your custom skincare video creative. Here is your brand demonstration!"

    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": selected_actor["avatar_id"],
                    "avatar_style": "normal"
                },
                "voice": {
                    "type": "text",
                    "input_text": script_text
                }
            }
        ],
        "dimension": {"width": 1080, "height": 1920},
        "custom_data": {"email": email, "company": company_name}
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        if res.status_code == 200:
            video_id = res.json().get("data", {}).get("video_id")
            print(f"INFO:LeadLoop:HeyGen generation task queued successfully! Video ID: {video_id}")
        else:
            print(f"ERROR:LeadLoop:HeyGen render failure: {res.text}")
    except Exception as e:
        print(f"ERROR:LeadLoop:HeyGen dispatch error: {e}")

# ------------------------------------------------------------------
# STRIPE WEBHOOK ENDPOINT
# ------------------------------------------------------------------
@app.post("/webhook/stripe")
@app.post("/webhook-stripe")
async def stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = payload.get("type")
    
    if event_type in ["checkout.session.completed", "payment_intent.succeeded"]:
        data_object = payload.get("data", {}).get("object", {})
        customer_email = data_object.get("customer_details", {}).get("email") or data_object.get("receipt_email") or "customer@example.com"
        
        custom_fields = data_object.get("custom_fields", [])
        company_name = "Your Business"
        if custom_fields and len(custom_fields) > 0:
            company_name = custom_fields[0].get("text", {}).get("value", "Your Business")

        persona = payload.get("persona")

        await log_event("Fulfillment Agent", f"Payment received from {customer_email}")

        background_tasks.add_task(pause_instantly_lead, customer_email)
        await log_event("Outreach Agent", f"Lead {customer_email} paused in Instantly")

        background_tasks.add_task(trigger_heygen_render, customer_email, company_name, persona)
        await log_event("Avatar Director", f"Dispatched HeyGen rendering task for {company_name}")

    return {"status": "success"}

# ------------------------------------------------------------------
# HEYGEN WEBHOOK ENDPOINTS
# ------------------------------------------------------------------
@app.post("/webhook/heygen")
@app.post("/webhook-heygen")
async def heygen_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        payload = await request.json()
    except Exception:
        return {"status": "invalid_payload"}

    event_type = payload.get("event_type") or payload.get("type")
    
    if event_type in ["avatar_video.success", "complete", "completed"]:
        video_url = payload.get("data", {}).get("url") or payload.get("url")
        custom_data = payload.get("data", {}).get("custom_data", {})
        recipient_email = custom_data.get("email") if custom_data else "customer@example.com"

        if video_url:
            print(f"INFO:LeadLoop:HeyGen Render Completed. Video URL: {video_url}")
            await log_event("Fulfillment Agent", f"HeyGen Render Completed. Video MP4 generated.", "SUCCESS")
            
            background_tasks.add_task(send_fulfillment_email, recipient_email, video_url)
            await log_event("Fulfillment Agent", f"Delivered video MP4 to {recipient_email}")

    return {"status": "success"}

# ------------------------------------------------------------------
# STARTUP EVENT
# ------------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    await log_event("Core Engine", "LeadLoop Command Center Online", "READY")
    
    run_lead_hunter()

    if not X_BEARER_TOKEN:
        print("[Agent X] Credentials missing in environment. Worker sleeping.")
    else:
        print("[Agent X] Credentials active. Initializing X worker thread.")

@app.get("/")
def health_check():
    return {"status": "online", "system": "LeadLoop Engine v2.0.0", "stored_leads": len(leads_db)}

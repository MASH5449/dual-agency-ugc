import os
import random
import logging
import asyncio
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, List, AsyncGenerator

import requests
import stripe
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

# ------------------------------------------------------------------------------
# LOGGING SETUP
# ------------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LeadLoop")

# ------------------------------------------------------------------------------
# ENVIRONMENT VARIABLES
# ------------------------------------------------------------------------------
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY")
INSTANTLY_API_KEY = os.getenv("INSTANTLY_API_KEY")
INSTANTLY_CAMPAIGN_ID = os.getenv("INSTANTLY_CAMPAIGN_ID", "")

# Porkbun SMTP details for automated fulfillment emails
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.porkbun.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "alex@useleadloop.xyz")
SMTP_PASS = os.getenv("SMTP_PASS", "")

BASE_URL = os.getenv("BASE_URL", "https://your-app.up.railway.app")

stripe.api_key = STRIPE_API_KEY

# ------------------------------------------------------------------------------
# REAL-TIME AGENT LOG STREAMING (For Next.js UI)
# ------------------------------------------------------------------------------
event_listeners: List[asyncio.Queue] = []

async def push_agent_event(agent_name: str, action: str, status: str = "ACTIVE"):
    """Broadcasts agent actions to connected Dashboard UI instances."""
    event_data = {
        "timestamp": asyncio.get_event_loop().time(),
        "agent": agent_name,
        "action": action,
        "status": status
    }
    logger.info(f"[{agent_name}] {action}")
    for queue in event_listeners:
        await queue.put(json.dumps(event_data))

# ------------------------------------------------------------------------------
# FASTAPI APP SETUP
# ------------------------------------------------------------------------------
app = FastAPI(title="LeadLoop Core Engine", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------------------
# HELPER FUNCTIONS
# ------------------------------------------------------------------------------

def get_public_avatar_and_voice():
    """Dynamically fetches active public avatar and voice IDs from HeyGen live API."""
    headers = {"X-Api-Key": HEYGEN_API_KEY}
    
    avatar_id = None
    voice_id = None
    
    # 1. Fetch active avatar
    try:
        avatar_res = requests.get("https://api.heygen.com/v2/avatars", headers=headers, timeout=5)
        if avatar_res.status_code == 200:
            avatars = avatar_res.json().get("data", {}).get("avatars", [])
            if avatars:
                selected_avatar = random.choice(avatars)
                avatar_id = selected_avatar.get("avatar_id")
    except Exception as e:
        logger.warning(f"Failed to fetch dynamic avatar: {e}")

    # 2. Fetch active voice
    try:
        voice_res = requests.get("https://api.heygen.com/v3/voices?type=public&language=English&limit=10", headers=headers, timeout=5)
        if voice_res.status_code == 200:
            voices = voice_res.json().get("data", [])
            if voices:
                selected_voice = random.choice(voices)
                voice_id = selected_voice.get("voice_id")
    except Exception as e:
        logger.warning(f"Failed to fetch dynamic voice: {e}")

    return avatar_id, voice_id


def stop_instantly_lead(email: str, campaign_id: str):
    """
    Calls Instantly API v2 to mark converted buyers as 'Won / Stopped'
    so follow-up email sequences halt immediately. Accepts 200 and 202.
    """
    if not INSTANTLY_API_KEY or not campaign_id:
        logger.warning("Instantly API Key or Campaign ID missing. Skipping auto-pause.")
        return False
        
    url = "https://api.instantly.ai/api/v2/leads/update-interest-status"
    headers = {
        "Authorization": f"Bearer {INSTANTLY_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "lead_email": email,
        "campaign_id": campaign_id,
        "interest_value": 3
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code in [200, 202]:
            logger.info(f"Successfully submitted lead pause job for {email} in Instantly (Status {response.status_code}).")
            return True
        else:
            logger.error(f"Instantly pause failed ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error calling Instantly API: {str(e)}")
        return False


def trigger_heygen_generation(customer_email: str, company_name: str):
    """Dynamically fetches active HeyGen avatar/voice IDs and triggers video generation."""
    if not HEYGEN_API_KEY:
        logger.error("HEYGEN_API_KEY environment variable is missing!")
        return None

    avatar_id, voice_id = get_public_avatar_and_voice()
    
    if not avatar_id or not voice_id:
        logger.error("Could not retrieve valid live Avatar ID or Voice ID from HeyGen.")
        return None

    url = "https://api.heygen.com/v2/video/generate"
    headers = {
        "X-Api-Key": HEYGEN_API_KEY,
        "Content-Type": "application/json"
    }
    
    script_text = (
        f"Hey there! Thanks for choosing LeadLoop. Here is your custom UGC video creative "
        f"engineered for {company_name}. Test this hook across TikTok and Instagram Reels today!"
    )
    
    # Clean host URL for webhook callback
    host_domain = BASE_URL.rstrip('/')
    callback_endpoint = f"{host_domain}/webhook/heygen"

    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": avatar_id,
                    "avatar_style": "normal"
                },
                "voice": {
                    "type": "text",
                    "input_text": script_text,
                    "voice_id": voice_id
                }
            }
        ],
        "dimension": {
            "width": 1080,
            "height": 1920
        },
        "callback_id": customer_email,
        "callback_url": callback_endpoint
    }
    
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        res_data = res.json()
        if res.status_code == 200 and res_data.get("data"):
            video_id = res_data.get("data", {}).get("video_id")
            logger.info(f"HeyGen generation task queued successfully! Video ID: {video_id}")
            return video_id
        else:
            logger.error(f"HeyGen API Error ({res.status_code}): {res_data}")
            return None
    except Exception as e:
        logger.error(f"Failed to connect to HeyGen: {str(e)}")
        return None


def send_fulfillment_email(to_email: str, video_url: str):
    """Sends the completed HeyGen MP4 link to the buyer via Porkbun SMTP."""
    if not SMTP_PASS:
        logger.warning("SMTP_PASS not set. Skipping delivery email dispatch.")
        return False

    msg = MIMEMultipart()
    msg['From'] = f"Alex Davis <{SMTP_USER}>"
    msg['To'] = to_email
    msg['Subject'] = "Your Custom AI UGC Video Creative is Ready! 🎬"

    body = f"""
    Hi there,

    Your automated UGC video ad creative is ready for download!

    You can access and download your full high-definition 9:16 video MP4 here:
    {video_url}

    If you have any questions or want to upgrade your creative package for next month, reply directly to this email.

    Best regards,
    Alex Davis
    LeadLoop Media
    """
    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()
        logger.info(f"Fulfillment email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send fulfillment email: {str(e)}")
        return False

# ------------------------------------------------------------------------------
# API ENDPOINTS
# ------------------------------------------------------------------------------

@app.get("/")
def health_check():
    return {"status": "online", "system": "LeadLoop Core Engine", "version": "2.0.0"}


@app.post("/webhook/stripe")
async def stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handles incoming Stripe webhooks for instant order fulfillment."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        else:
            event = json.loads(payload)
    except Exception as e:
        logger.error(f"Webhook signature error: {str(e)}")
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        
        customer_email = session.get("customer_details", {}).get("email") or session.get("customer_email")
        company_name = session.get("custom_fields", [{}])[0].get("text", {}).get("value") or "your brand"

        logger.info(f"Payment Received from {customer_email} for {company_name}")
        
        # 1. Update UI Console Feed
        background_tasks.add_task(push_agent_event, "Fulfillment Agent", f"Payment received from {customer_email}")

        # 2. Auto-Pause Lead in Instantly
        if customer_email and INSTANTLY_CAMPAIGN_ID:
            background_tasks.add_task(stop_instantly_lead, customer_email, INSTANTLY_CAMPAIGN_ID)
            background_tasks.add_task(push_agent_event, "Outreach Agent", f"Lead {customer_email} paused in Instantly")

        # 3. Trigger HeyGen Rendering Pipeline
        if customer_email:
            background_tasks.add_task(trigger_heygen_generation, customer_email, company_name)
            background_tasks.add_task(push_agent_event, "Avatar Director", f"Dispatched HeyGen rendering task for {company_name}")

    return {"status": "success"}


@app.post("/webhook/heygen")
async def heygen_webhook(request: Request, background_tasks: BackgroundTasks):
    """Webhook callback from HeyGen when video processing completes."""
    payload = await request.json()
    event_type = payload.get("event_type")

    if event_type == "avatar_video.success":
        event_data = payload.get("event_data", {})
        video_url = event_data.get("url")
        customer_email = payload.get("callback_id") or event_data.get("callback_id")

        logger.info(f"HeyGen Render Completed. Video URL: {video_url}")

        if customer_email and video_url:
            background_tasks.add_task(send_fulfillment_email, customer_email, video_url)
            background_tasks.add_task(push_agent_event, "Fulfillment Agent", f"Delivered video MP4 to {customer_email}", "COMPLETED")

    return {"status": "ok"}


@app.get("/api/stream")
async def stream_agent_events():
    """Server-Sent Events (SSE) endpoint to drive real-time Next.js UI Dashboard updates."""
    async def event_generator() -> AsyncGenerator[str, None]:
        queue = asyncio.Queue()
        event_listeners.append(queue)
        try:
            while True:
                data = await queue.get()
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            event_listeners.remove(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# ------------------------------------------------------------------------------
# SERVER RUNNER
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

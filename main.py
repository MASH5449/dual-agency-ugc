import os
import json
import random
import asyncio
import smtplib
import requests
import threading
import time
from typing import AsyncGenerator
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from bs4 import BeautifulSoup
from openai import OpenAI

app = FastAPI(title="LeadLoop Engine API", version="4.3.1")

# Enable CORS for incoming webhooks and clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared event queue for SSE streaming logs
event_queue = asyncio.Queue()

# In-Memory Storage
leads_db = []
generation_jobs = {}  # Maps HeyGen video_id -> recipient email

# Environment Variables
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
INSTANTLY_API_KEY = os.getenv("INSTANTLY_API_KEY", "")
INSTANTLY_CAMPAIGN_ID = os.getenv("INSTANTLY_CAMPAIGN_ID", "")
HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.porkbun.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")

# Imported Female ElevenLabs Voice ID registered inside HeyGen
HEYGEN_VOICE_ID = "7215dd7ccfcb432ea5659ab4390d162d"

# Initialize OpenAI Client
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ------------------------------------------------------------------
# VERIFIED FEMALE AVATAR LOOK RESOLVER (BULLETPROOF GENDER MATCH)
# ------------------------------------------------------------------
VERIFIED_FEMALE_LOOKS = [
    "745b64340bdf4789b3927c11f3911b2f",  # Female UGC Model
    "e76c0b3918a14866aa95e01abd339204",  # Female Creator
    "3b76a084c6794692a7e78d150b06ba67",  # Female Casual
    "3f3a1f33f38148bfae345b69f64a2e5d"   # Female Creator Look
]

def get_valid_female_v3_look() -> str:
    """
    Fetches avatar looks from HeyGen V3 and strictly filters for female models.
    Falls back exclusively to verified female IDs to guarantee gender matching.
    """
    if not HEYGEN_API_KEY:
        return random.choice(VERIFIED_FEMALE_LOOKS)

    url = "https://api.heygen.com/v3/avatars/looks"
    headers = {"X-Api-Key": HEYGEN_API_KEY}

    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            raw_data = res.json().get("data", [])
            looks_list = raw_data if isinstance(raw_data, list) else raw_data.get("looks", [])

            female_looks = []
            for item in looks_list:
                if isinstance(item, dict):
                    look_id = item.get("id") or item.get("look_id")
                    name = str(item.get("name", "")).lower()
                    tags = str(item.get("tags", "")).lower()
                    gender = str(item.get("gender", "")).lower()

                    meta = f"{name} {tags} {gender}"

                    if any(m in meta for m in ["male", " man ", " guy ", "boy", "david", "john", "michael", "alex", "james"]):
                        continue

                    if any(f in meta for f in ["female", "woman", "girl", "lady", "sarah", "emma", "chloe", "ugc"]):
                        if look_id:
                            female_looks.append(look_id)

            if female_looks:
                selected = random.choice(female_looks)
                print(f"[Avatar Director] Isolated {len(female_looks)} female looks. Selected: {selected}")
                return selected

    except Exception as e:
        print(f"ERROR:LeadLoop:Dynamic avatar lookup failed: {e}")

    fallback_selected = random.choice(VERIFIED_FEMALE_LOOKS)
    print(f"[Avatar Director] Using guaranteed female fallback look ID: {fallback_selected}")
    return fallback_selected

# ------------------------------------------------------------------
# DYNAMIC WEB SCRAPER & GPT-4o SCRIPT ENGINE
# ------------------------------------------------------------------
def scrape_brand_website(website_url: str) -> str:
    """
    Extracts core text content from the brand's landing page.
    """
    if not website_url:
        return ""

    if not website_url.startswith(("http://", "https://")):
        website_url = f"https://{website_url}"

    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(website_url, headers=headers, timeout=8)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for element in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                element.decompose()
            text = soup.get_text(separator=" ")
            clean_text = " ".join(text.split())
            print(f"[Scraper Agent] Successfully extracted landing page content from {website_url}")
            return clean_text[:2500]
    except Exception as e:
        print(f"[Scraper Agent] Failed to scrape {website_url}: {e}")

    return ""

def generate_dynamic_ai_script(company_name: str, website_url: str = "") -> str:
    """
    Scrapes landing page text and uses GPT-4o to write a custom UGC script.
    """
    brand_context = scrape_brand_website(website_url)

    if not openai_client:
        print("[AI Writer Agent] OpenAI API Key missing. Falling back to dynamic baseline script.")
        return f"Hey {company_name} team! Your social video ads could convert significantly higher with authentic UGC. Here is a custom video creative concept we built specifically for {company_name}!"

    prompt = f"""
    You are an expert direct-response UGC ad copywriter for DTC skincare and cosmetics brands.

    Brand Name: {company_name}
    Brand Website Content: {brand_context if brand_context else 'No website text provided.'}

    Task: Write a high-converting 30-second UGC video ad script for {company_name}.

    Structure:
    1. HOOK (0-3s): Stop the scroll immediately with a bold claim, hook, or relatable pain point.
    2. BODY (3-20s): Address the core skin problem, introduce {company_name}'s product, and highlight 2 specific ingredients/benefits.
    3. CTA (20-30s): Natural, clear push to click the link and try it today.

    Constraints:
    - Conversational, authentic human tone (sounds like a real creator on TikTok/Reels).
    - Absolutely NO scene directions, camera notes, or brackets like [smiling] or (pause). Return ONLY spoken script text.
    - Word count: Strict 60 to 75 words total (fits 30s audio speech).
    """

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a world-class DTC UGC ad copywriter."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=180
        )
        script = response.choices[0].message.content.strip()
        print(f"[AI Writer Agent] GPT-4o generated custom script for '{company_name}'")
        return script
    except Exception as e:
        print(f"ERROR:LeadLoop:OpenAI script generation failed: {e}")
        return f"Hey {company_name} team! Standard product shots get glossed over in feeds. Here is a tailored UGC concept built for {company_name} to drive higher click-through rates."

# ------------------------------------------------------------------
# INSTANTLY AUTOMATED LEAD PUSH HELPER (FIXED API v2 PAYLOAD)
# ------------------------------------------------------------------
def push_x_lead_to_instantly(company_name: str, handle: str, profile_url: str):
    """
    Pushes leads discovered by Agent X directly into Instantly.ai campaigns.
    """
    if not INSTANTLY_API_KEY or not INSTANTLY_CAMPAIGN_ID:
        print("[Agent X] Skipping Instantly push: Missing INSTANTLY_API_KEY or INSTANTLY_CAMPAIGN_ID.")
        return

    url = "https://api.instantly.ai/api/v2/leads"
    headers = {
        "Authorization": f"Bearer {INSTANTLY_API_KEY}",
        "Content-Type": "application/json"
    }

    clean_handle = handle.replace("@", "").strip().lower()
    valid_email = f"{clean_handle}@twitter.com"

    payload = {
        "email": valid_email,
        "first_name": company_name,
        "company_name": company_name,
        "campaign_id": INSTANTLY_CAMPAIGN_ID,
        "skip_if_in_workspace": True,
        "custom_variables": {
            "twitter_handle": handle,
            "profile_url": profile_url
        }
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        if res.status_code in [200, 201]:
            print(f"[Agent X] Successfully pushed lead '{company_name}' ({handle}) to Instantly.")
        else:
            print(f"[Agent X] Failed to push lead to Instantly (Status {res.status_code}): {res.text}")
    except Exception as e:
        print(f"[Agent X] Error pushing lead to Instantly: {e}")

# ------------------------------------------------------------------
# AGENT X: TWITTER/X PROSPECTING WORKER
# ------------------------------------------------------------------
def run_agent_x_worker():
    if not X_BEARER_TOKEN:
        print("[Agent X] Credentials missing in environment. Worker sleeping.")
        return

    print("[Agent X] Worker thread active. Starting periodic prospect searches...")

    search_queries = [
        '(skincare OR cosmetics) ("looking for UGC" OR "need video ads" OR "UGC creators")',
        '("DTC skincare" OR "beauty brand") ("scaling ads" OR "ROAS" OR "creative testing")'
    ]

    headers = {
        "Authorization": f"Bearer {X_BEARER_TOKEN}",
        "User-Agent": "LeadLoopAgentX/1.0"
    }

    while True:
        try:
            query = random.choice(search_queries)
            url = f"https://api.twitter.com/2/tweets/search/recent?query={requests.utils.quote(query)}&max_results=10&tweet.fields=created_at,author_id&expansions=author_id&user.fields=username,name,description"

            response = requests.get(url, headers=headers, timeout=10)

            if response.status_code == 200:
                data = response.json()
                tweets = data.get("data", [])
                users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}

                found_count = 0
                for tweet in tweets:
                    author_id = tweet.get("author_id")
                    author_info = users.get(author_id, {})
                    username = author_info.get("username", "unknown")
                    name = author_info.get("name", "Brand Lead")
                    profile_url = f"https://x.com/{username}"
                    handle = f"@{username}"

                    lead_entry = {
                        "source": "Agent X (Twitter)",
                        "company": name,
                        "handle": handle,
                        "tweet_id": tweet.get("id"),
                        "text": tweet.get("text"),
                        "profile_url": profile_url
                    }

                    if not any(l.get("tweet_id") == tweet.get("id") for lattr in leads_db for l in [lattr] if isinstance(l, dict)):
                        leads_db.append(lead_entry)
                        found_count += 1

                        # Auto-push newly discovered lead directly to Instantly
                        push_x_lead_to_instantly(name, handle, profile_url)

                print(f"[Agent X] Search completed for query: '{query[:30]}...'. Found {found_count} new leads. Total in DB: {len(leads_db)}")
            elif response.status_code == 429:
                print("[Agent X] Rate limit reached. Backing off until next cycle.")
            elif response.status_code == 402:
                print("[Agent X] Payment required (402). Ensure X developer account credits are active.")
            else:
                print(f"[Agent X] API Error ({response.status_code}): {response.text}")

        except Exception as e:
            print(f"[Agent X] Execution error: {e}")

        time.sleep(900)

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
# LEAD HUNTER WORKER
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
# SMTP FULFILLMENT EMAIL
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
def trigger_heygen_render(email: str, company_name: str, website_url: str = ""):
    if not HEYGEN_API_KEY:
        print("[Avatar Director] Skipping HeyGen render: No API Key provided.")
        return

    avatar_id = get_valid_female_v3_look()
    
    # Generate website-aware AI script via GPT-4o
    script_text = generate_dynamic_ai_script(company_name, website_url)

    print(f"[Avatar Director] Dispatching V3 render for '{company_name}' using female look: {avatar_id}")

    url = "https://api.heygen.com/v3/videos"
    headers = {
        "X-Api-Key": HEYGEN_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "type": "avatar",
        "avatar_id": avatar_id,
        "script": script_text,
        "voice_id": HEYGEN_VOICE_ID,
        "aspect_ratio": "9:16"
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        if res.status_code == 200:
            res_data = res.json().get("data", {})
            video_id = res_data.get("video_id") or res_data.get("id")

            if video_id:
                generation_jobs[video_id] = email

            print(f"INFO:LeadLoop:HeyGen V3 video generation task queued successfully! Video ID: {video_id}")
        else:
            print(f"ERROR:LeadLoop:HeyGen render failure: {res.text}")
    except Exception as e:
        print(f"ERROR:LeadLoop:HeyGen dispatch error: {e}")

# ------------------------------------------------------------------
# TIER FULFILLMENT BATCH PROCESSOR
# ------------------------------------------------------------------
def process_tier_fulfillment(customer_email: str, company_name: str, website_url: str, tier: str):
    if tier == "Growth":
        video_count = 10
    elif tier == "Enterprise":
        video_count = 30
    else:
        video_count = 1

    print(f"[Fulfillment Agent] Starting batch fulfillment for {customer_email} ({tier} Tier -> {video_count} video(s))")

    for i in range(video_count):
        trigger_heygen_render(customer_email, company_name, website_url)
        if video_count > 1:
            time.sleep(2)

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

        # Parse Stripe Custom Fields for Company Name and Website URL
        custom_fields = data_object.get("custom_fields", [])
        company_name = "Your Business"
        website_url = ""

        for field in custom_fields:
            label = str(field.get("label", {}).get("custom", "")).lower()
            val = str(field.get("text", {}).get("value", ""))
            
            if any(k in label for k in ["company", "brand", "business"]):
                company_name = val if val else company_name
            elif any(k in label for k in ["website", "url", "site"]):
                website_url = val

        amount_paid = data_object.get("amount_total", 0) / 100

        if amount_paid >= 800:
            tier = "Enterprise"
        elif amount_paid >= 300:
            tier = "Growth"
        else:
            tier = "Starter"

        await log_event("Fulfillment Agent", f"Payment received from {customer_email} for {tier} Tier (${amount_paid:.0f})")

        background_tasks.add_task(pause_instantly_lead, customer_email)
        await log_event("Outreach Agent", f"Lead {customer_email} paused in Instantly")

        background_tasks.add_task(process_tier_fulfillment, customer_email, company_name, website_url, tier)
        await log_event("Avatar Director", f"Queued {tier} Tier batch render for {company_name}")

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
        video_url = payload.get("data", {}).get("url") or payload.get("url") or payload.get("data", {}).get("video_url")
        video_id = payload.get("data", {}).get("video_id") or payload.get("video_id") or payload.get("data", {}).get("id")

        recipient_email = generation_jobs.get(video_id, "customer@example.com")

        if video_url:
            print(f"INFO:LeadLoop:HeyGen Render Completed. Video URL: {video_url}")
            await log_event("Fulfillment Agent", f"HeyGen Render Completed. Video MP4 generated.", "SUCCESS")

            background_tasks.add_task(send_fulfillment_email, recipient_email, video_url)
            await log_event("Fulfillment Agent", f"Delivered video MP4 to {recipient_email}")

    return {"status": "success"}

# ------------------------------------------------------------------
# STARTUP EVENT & API ROUTES
# ------------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    await log_event("Core Engine", "LeadLoop Command Center Online", "READY")
    run_lead_hunter()

    if X_BEARER_TOKEN:
        x_thread = threading.Thread(target=run_agent_x_worker, daemon=True)
        x_thread.start()
        print("[Agent X] Credentials active. Initializing X worker thread.")
    else:
        print("[Agent X] Credentials missing in environment. Worker sleeping.")

@app.get("/")
def health_check():
    return {
        "status": "online",
        "system": "LeadLoop Engine v4.3.1",
        "stored_leads": len(leads_db),
        "agent_x_active": bool(X_BEARER_TOKEN),
        "openai_active": bool(OPENAI_API_KEY)
    }

@app.get("/leads")
def get_leads():
    """Returns all stored leads extracted by Agent X."""
    return {"total": len(leads_db), "leads": leads_db}

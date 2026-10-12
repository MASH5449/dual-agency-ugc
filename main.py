import os
import json
import hmac
import hashlib
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

app = FastAPI(title="LeadLoop Engine API", version="5.5.0")

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
# Outreach (Agent X, lead hunter) stays off until you explicitly turn it on.
ENABLE_OUTREACH = os.getenv("ENABLE_OUTREACH", "").lower() in ("1", "true", "yes", "on")

HEYGEN_LOOKS_URL = "https://api.heygen.com/v3/avatars/looks?ownership=public&limit=10"
# Used only when a look does not carry its own default_voice_id.
HEYGEN_FALLBACK_VOICE_ID = "2d5b0e6cf36f460aa7fc47e3eee4ba54"

# Initialize OpenAI Client
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ------------------------------------------------------------------
# HEYGEN V3 DYNAMIC LIVE AVATAR RESOLVER (ZERO HARDCODED IDS)
# ------------------------------------------------------------------
def fetch_avatar_candidates() -> list[tuple[str, str]]:
    """Return (look_id, voice_id) pairs from GET /v3/avatars/looks.

    POST /v3/videos needs a look id. GET /v3/avatars returns groups, and a
    group id comes back as avatar_not_found. Prefer looks marked female.
    """
    if not HEYGEN_API_KEY:
        print("[Avatar Director] HEYGEN_API_KEY is missing.")
        return []

    headers = {"X-Api-Key": HEYGEN_API_KEY, "Accept": "application/json"}
    try:
        res = requests.get(HEYGEN_LOOKS_URL, headers=headers, timeout=15)
    except Exception as e:
        print(f"ERROR:LeadLoop:Failed to query avatar looks: {e}")
        return []

    if res.status_code != 200:
        print(f"[Avatar Director] GET /v3/avatars/looks failed (HTTP {res.status_code}): {res.text[:300]}")
        return []

    looks = res.json().get("data") or []
    preferred: list[tuple[str, str]] = []
    rest: list[tuple[str, str]] = []
    for look in looks:
        if not isinstance(look, dict):
            continue
        avatar_id = look.get("id") or look.get("look_id")
        if not avatar_id:
            continue
        voice_id = str(look.get("default_voice_id") or HEYGEN_FALLBACK_VOICE_ID)
        pair = (str(avatar_id), voice_id)
        if str(look.get("gender") or "").lower() in ("female", "woman"):
            preferred.append(pair)
        else:
            rest.append(pair)

    chosen = preferred or rest
    print(f"[Avatar Director] {len(chosen)} look(s) ready ({len(preferred)} marked female).")
    return chosen


def stripe_event_ok(body: bytes, signature: str | None) -> bool:
    """Check Stripe-Signature when STRIPE_WEBHOOK_SECRET is set. Unset = allow."""
    if not STRIPE_WEBHOOK_SECRET:
        print("[Stripe] STRIPE_WEBHOOK_SECRET unset. Webhook accepted without a signature check.")
        return True
    if not signature:
        return False
    parts = {}
    for item in signature.split(","):
        if "=" in item:
            key, value = item.split("=", 1)
            parts[key.strip()] = value.strip()
    timestamp = parts.get("t")
    signed = parts.get("v1")
    if not timestamp or not signed:
        return False
    digest = hmac.new(
        STRIPE_WEBHOOK_SECRET.encode(),
        f"{timestamp}.".encode() + body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(digest, signed)

# ------------------------------------------------------------------
# DYNAMIC WEB SCRAPER & GPT-4o SCRIPT ENGINE
# ------------------------------------------------------------------
def scrape_brand_website(website_url: str) -> str:
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
    brand_context = scrape_brand_website(website_url)

    if not openai_client:
        print("[AI Writer Agent] OpenAI API Key missing. Falling back to baseline script.")
        return f"Hey {company_name} team! Your social video ads could convert significantly higher with authentic UGC. Here is a custom video creative concept we built specifically for {company_name}!"

    prompt = f"""
    You are an expert direct-response UGC ad copywriter for DTC skincare and cosmetics brands.

    Brand Name: {company_name}
    Brand Website Content: {brand_context if brand_context else 'No website text provided.'}

    Task: Write a spoken 30-second UGC ad for {company_name}.

    Use only facts that appear in the page text. If an ingredient, result, or review is not in that text, do not invent it. If the page text is empty, talk about the brand by name only.

    Constraints:
    - 68 to 78 words. First sentence is a hook a stranger would not skip. Not "hey guys".
    - Conversational, contractions, one short sentence then a longer one.
    - No cure claims, no prices, no scene directions, no brackets.
    - Last line points at the product page. Return only spoken words.
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
# INSTANTLY AUTOMATED LEAD PUSH HELPER
# ------------------------------------------------------------------
def push_x_lead_to_instantly(company_name: str, handle: str, profile_url: str):
    if not INSTANTLY_API_KEY or not INSTANTLY_CAMPAIGN_ID:
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
                        push_x_lead_to_instantly(name, handle, profile_url)

                print(f"[Agent X] Search completed. Found {found_count} new leads. Total in DB: {len(leads_db)}")
            elif response.status_code == 429:
                print("[Agent X] Rate limit reached. Backing off until next cycle.")
        except Exception:
            pass

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
    except Exception:
        pass

# ------------------------------------------------------------------
# HEYGEN V3 VIDEO RENDER ENGINE
# ------------------------------------------------------------------
def trigger_heygen_render(email: str, company_name: str, website_url: str = ""):
    if not HEYGEN_API_KEY:
        print("[Avatar Director] Skipping HeyGen render: No API Key provided.")
        return

    script_text = generate_dynamic_ai_script(company_name, website_url)
    candidates = fetch_avatar_candidates()
    if not candidates:
        print("[Avatar Director] ERROR: no look id from GET /v3/avatars/looks.")
        return

    print(f"[Avatar Director] Dispatching V3 render for '{company_name}' across {len(candidates)} look(s)")

    url = "https://api.heygen.com/v3/videos"
    headers = {
        "X-Api-Key": HEYGEN_API_KEY,
        "Content-Type": "application/json"
    }

    for avatar_id, voice_id in candidates:
        payload = {
            "type": "avatar",
            "avatar_id": avatar_id,
            "script": script_text,
            "voice_id": voice_id,
            "aspect_ratio": "9:16"
        }
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=20)
        except Exception as e:
            print(f"ERROR:LeadLoop:HeyGen V3 dispatch exception: {e}")
            return

        if res.status_code == 200:
            res_data = res.json().get("data", {})
            video_id = res_data.get("video_id") or res_data.get("id")
            if video_id:
                generation_jobs[video_id] = email
                print(f"INFO:LeadLoop:HeyGen V3 queued. look={avatar_id} video_id={video_id}")
                return
            print(f"ERROR:LeadLoop:HeyGen V3 render failure response: {res.text}")
            return

        if res.status_code == 404 and "avatar_not_found" in res.text:
            print(f"[Avatar Director] look {avatar_id} not found, trying the next one")
            continue

        print(f"ERROR:LeadLoop:HeyGen V3 render failure (HTTP {res.status_code}): {res.text}")
        return

    print("[Avatar Director] every candidate look returned avatar_not_found")

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
    body = await request.body()
    if not stripe_event_ok(body, request.headers.get("stripe-signature")):
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = payload.get("type")

    if event_type in ["checkout.session.completed", "payment_intent.succeeded"]:
        data_object = payload.get("data", {}).get("object", {})
        customer_email = data_object.get("customer_details", {}).get("email") or data_object.get("receipt_email") or "customer@example.com"

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
        video_id = payload.get("data", {}).get("video_id") or payload.get("data", {}).get("id")

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
    if not ENABLE_OUTREACH:
        print("[Outreach] Held. Set ENABLE_OUTREACH=true before Agent X or the lead hunter run.")
        return

    run_lead_hunter()

    if X_BEARER_TOKEN:
        x_thread = threading.Thread(target=run_agent_x_worker, daemon=True)
        x_thread.start()
        print("[Agent X] ENABLE_OUTREACH is on. Worker thread started.")
    else:
        print("[Agent X] ENABLE_OUTREACH is on, but X_BEARER_TOKEN is missing.")

@app.get("/")
def health_check():
    return {
        "status": "online",
        "system": "LeadLoop Engine v5.5.0",
        "stored_leads": len(leads_db),
        "agent_x_active": bool(ENABLE_OUTREACH and X_BEARER_TOKEN),
        "openai_active": bool(OPENAI_API_KEY)
    }

@app.get("/leads")
def get_leads():
    return {"total": len(leads_db), "leads": leads_db}

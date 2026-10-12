import os
import stripe
from fastapi import FastAPI, Request, HTTPException
from video_pipeline import generate_video
from instantly_pipeline import push_lead_to_instantly

GENERIC_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
SECRET_QUICK = os.getenv("STRIPE_WEBHOOK_SECRET_QUICK") or GENERIC_SECRET
SECRET_SCALE = os.getenv("STRIPE_WEBHOOK_SECRET_SCALE") or GENERIC_SECRET
SECRET_WHITELABEL = os.getenv("STRIPE_WEBHOOK_SECRET_WHITELABEL") or GENERIC_SECRET

stripe.api_key = os.getenv("STRIPE_API_KEY")
app = FastAPI()

# In-memory mapping of video_id -> customer_email
JOBS_DB = {}

@app.post("/webhook-quick")
async def webhook_quick(request: Request):
    return await verify_and_handle(request, SECRET_QUICK, "quick")

@app.post("/webhook-scale")
async def webhook_scale(request: Request):
    return await verify_and_handle(request, SECRET_SCALE, "scale")

@app.post("/webhook-whitelabel")
async def webhook_whitelabel(request: Request):
    return await verify_and_handle(request, SECRET_WHITELABEL, "whitelabel")

async def verify_and_handle(request: Request, secret: str, expected_tier: str):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    return handle_stripe_event(event, expected_tier)

def handle_stripe_event(event, expected_tier):
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        metadata = session.get("metadata", {})
        actual_tier = metadata.get("tier", "").lower()

        if actual_tier != expected_tier:
            print(f"[Webhook {expected_tier.capitalize()}] Ignored event for tier: '{actual_tier}'")
            return {"status": "ignored"}

        customer_email = session.get("customer_details", {}).get("email", "unknown@test.com")
        product_url = metadata.get("product_url", "google.com")
        print(f"[Webhook {expected_tier.capitalize()}] Processing order for {customer_email}")
        
        # Trigger Video Generation
        res = generate_video(product_url, actual_tier)
        video_id = res.get("data", {}).get("video_id") or res.get("data", {}).get("id")
        
        if video_id and video_id != "N/A":
            JOBS_DB[video_id] = customer_email
            print(f"[Job Saved] Mapped video_id {video_id} -> {customer_email}")

    return {"status": "success"}

# --- HEYGEN COMPLETION WEBHOOK ---
@app.post("/webhook-heygen")
async def webhook_heygen(request: Request):
    data = await request.json()
    event_type = data.get("event_type") or data.get("type")
    
    # HeyGen completion event
    if event_type in ["avatar_video.success", "video.completed"]:
        msg_data = data.get("event_data", {}) or data.get("data", {})
        video_id = msg_data.get("video_id") or msg_data.get("id")
        video_url = msg_data.get("url") or msg_data.get("video_url")
        
        customer_email = JOBS_DB.get(video_id, "unknown@test.com")
        print(f"[HeyGen Webhook] Render Complete for Video ID: {video_id} -> URL: {video_url}")
        
        # Dispatch to Instantly.ai
        push_lead_to_instantly(customer_email, video_url)
        
    return {"status": "success"}

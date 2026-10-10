import os
import stripe
from fastapi import FastAPI, Request, HTTPException
from video_pipeline import generate_video

# 1. Pull the specific endpoint secrets from your Railway variables
SECRET_QUICK = os.getenv("STRIPE_WEBHOOK_SECRET_QUICK")
SECRET_SCALE = os.getenv("STRIPE_WEBHOOK_SECRET_SCALE")
SECRET_WHITELABEL = os.getenv("STRIPE_WEBHOOK_SECRET_WHITELABEL")

stripe.api_key = os.getenv("STRIPE_API_KEY")
app = FastAPI()

# --- QUICK ENDPOINT ---
@app.post("/webhook-quick")
async def webhook_quick(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    try:
        # Uses the QUICK secret
        event = stripe.Webhook.construct_event(payload, sig_header, SECRET_QUICK)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    return handle_event(event, "quick")

# --- SCALE ENDPOINT ---
@app.post("/webhook-scale")
async def webhook_scale(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    try:
        # Uses the SCALE secret
        event = stripe.Webhook.construct_event(payload, sig_header, SECRET_SCALE)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    return handle_event(event, "scale")

# --- WHITELABEL ENDPOINT ---
@app.post("/webhook-whitelabel")
async def webhook_whitelabel(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    try:
        # Uses the WHITELABEL secret
        event = stripe.Webhook.construct_event(payload, sig_header, SECRET_WHITELABEL)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    return handle_event(event, "whitelabel")


# --- CORE LOGIC ---
def handle_event(event, expected_tier):
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        metadata = session.get("metadata", {})
        actual_tier = metadata.get("tier", "").lower()

        if actual_tier != expected_tier:
            print(f"[Webhook {expected_tier.capitalize()}] Ignored event for tier: '{actual_tier}'")
            return {"status": "ignored"}

        customer_email = session.get("customer_details", {}).get("email", "unknown")
        print(f"[Webhook {expected_tier.capitalize()}] Processing {expected_tier.capitalize()} order for {customer_email}")
        
        product_url = metadata.get("product_url", "google.com")
        
        # Trigger Video
        generate_video(product_url, actual_tier)

    return {"status": "success"}

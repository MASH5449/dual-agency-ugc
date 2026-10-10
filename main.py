import os
import stripe
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from apscheduler.schedulers.background import BackgroundScheduler
from video_pipeline import generate_video

app = FastAPI(title="Dual-Agency Backend Engine")

# Background Scheduler for Upsell Emails
scheduler = BackgroundScheduler()
scheduler.start()

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
stripe.api_key = STRIPE_SECRET_KEY

INSTANTLY_API_KEY = os.getenv("INSTANTLY_API_KEY")
STRIPE_500_LINK = os.getenv("STRIPE_500_LINK")
STRIPE_1000_LINK = os.getenv("STRIPE_1000_LINK")

def send_upsell_email(customer_email: str, upsell_link: str, offer_name: str):
    """Enrolls customer into Instantly campaign 72 hours post-purchase."""
    url = "https://api.instantly.ai/api/v1/lead/add"
    payload = {
        "api_key": INSTANTLY_API_KEY,
        "email": customer_email,
        "custom_variables": {
            "upsell_link": upsell_link,
            "offer_name": offer_name
        }
    }
    try:
        requests.post(url, json=payload, timeout=10)
        print(f"[Upsell Engine] Enrolled {customer_email} into 72hr sequence for {offer_name}")
    except Exception as e:
        print(f"[Upsell Engine] Error enrolling {customer_email}: {e}")

def parse_webhook_payload(request_body: bytes, sig_header: str, secret: str):
    """Validates Stripe signature against specific endpoint secret."""
    try:
        if secret:
            return stripe.Webhook.construct_event(request_body, sig_header, secret)
        return stripe.Event.construct_from(stripe.json.loads(request_body), stripe.api_key)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook Validation Error: {e}")

@app.get("/")
def health_check():
    return {"status": "Dual-Agency Backend Online", "option": "B - Dedicated Endpoints"}

# --- ENDPOINT 1: $150 Fast-Track Offer ---
@app.post("/webhook-quick")
async def webhook_quick(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET_QUICK")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {str(e)}")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        tier = session.get("metadata", {}).get("tier", "quick")

        if tier != "quick":
            print(f"[Webhook Quick] Ignored event for tier: '{tier}'")
            return {"status": "ignored", "reason": f"Tier is {tier}"}

        print(f"[Webhook Quick] Processing Quick order for {session.get('customer_details', {}).get('email')}")
        # Keep your existing pipeline call here:
        execute_video_pipeline(session, tier="quick")

    return {"status": "success"}
        # 1. Trigger $150 Video Pipeline
        generate_video(product_url, tier="quick")
        
        # 2. Schedule 72-hour Upsell Email ($500 Bundle)
        run_date = datetime.now() + timedelta(hours=72)
        scheduler.add_job(
            send_upsell_email, 
            'date', 
            run_date=run_date, 
            args=[customer_email, STRIPE_500_LINK, "Growth Ad Bundle ($500)"]
        )

    return {"status": "success"}

# --- ENDPOINT 2: $500 Growth Bundle ---
@app.post("/webhook-scale")
async def webhook_scale(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET_SCALE")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {str(e)}")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        tier = session.get("metadata", {}).get("tier", "scale")

        if tier != "scale":
            print(f"[Webhook Scale] Ignored event for tier: '{tier}'")
            return {"status": "ignored", "reason": f"Tier is {tier}"}

        print(f"[Webhook Scale] Processing Scale order for {session.get('customer_details', {}).get('email')}")
        # Keep your existing pipeline call here:
        execute_video_pipeline(session, tier="scale")

    return {"status": "success"}

        # 1. Trigger $500 Video Pipeline (15 Concept Variations)
        generate_video(product_url, tier="scale")
        
        # 2. Schedule 72-hour Upsell Email ($1,000/mo Retainer)
        run_date = datetime.now() + timedelta(hours=72)
        scheduler.add_job(
            send_upsell_email, 
            'date', 
            run_date=run_date, 
            args=[customer_email, STRIPE_1000_LINK, "Monthly UGC Retainer ($1,000/mo)"]
        )

    return {"status": "success"}

# --- ENDPOINT 3: $2,500 Agency Whitelabel ---
@app.post("/webhook-whitelabel")
async def webhook_whitelabel(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET_WHITELABEL")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {str(e)}")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        tier = session.get("metadata", {}).get("tier", "whitelabel")

        if tier != "whitelabel":
            print(f"[Webhook Whitelabel] Ignored event for tier: '{tier}'")
            return {"status": "ignored", "reason": f"Tier is {tier}"}

        print(f"[Webhook Whitelabel] Processing Whitelabel order for {session.get('customer_details', {}).get('email')}")
        # Keep your existing pipeline call here:
        execute_video_pipeline(session, tier="whitelabel")

    return {"status": "success"}
        # Trigger $2,500 Whitelabel Pipeline (60 Variations)
        generate_video("https://example.com", tier="whitelabel")

    return {"status": "success"}

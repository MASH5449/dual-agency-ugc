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
    body = await request.body()
    sig_header = request.headers.get("stripe-signature")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET_QUICK")
    
    event = parse_webhook_payload(body, sig_header, secret)
    
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        customer_email = session.get("customer_details", {}).get("email")
        
        custom_fields = session.get("custom_fields", [])
        product_url = "https://example.com"
        for field in custom_fields:
            if field.get("label", {}).get("custom") == "Product URL" or field.get("key") == "producturl":
                product_url = field.get("text", {}).get("value") or product_url

        print(f"[Webhook Quick] Order received from {customer_email} for URL: {product_url}")
        
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
    body = await request.body()
    sig_header = request.headers.get("stripe-signature")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET_SCALE")
    
    event = parse_webhook_payload(body, sig_header, secret)
    
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        customer_email = session.get("customer_details", {}).get("email")
        
        custom_fields = session.get("custom_fields", [])
        product_url = "https://example.com"
        for field in custom_fields:
            if field.get("label", {}).get("custom") == "Product URL" or field.get("key") == "producturl":
                product_url = field.get("text", {}).get("value") or product_url

        print(f"[Webhook Scale] Order received from {customer_email} for URL: {product_url}")

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
    body = await request.body()
    sig_header = request.headers.get("stripe-signature")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET_WHITELABEL")
    
    event = parse_webhook_payload(body, sig_header, secret)
    
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        customer_email = session.get("customer_details", {}).get("email")
        
        custom_fields = session.get("custom_fields", [])
        agency_name = "Whitelabel Partner"
        for field in custom_fields:
            if "Agency" in field.get("label", {}).get("custom", ""):
                agency_name = field.get("text", {}).get("value") or agency_name

        print(f"[Webhook Whitelabel] Order received from {agency_name} ({customer_email})")
        
        # Trigger $2,500 Whitelabel Pipeline (60 Variations)
        generate_video("https://example.com", tier="whitelabel")

    return {"status": "success"}

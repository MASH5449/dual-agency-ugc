import os
import sys
import asyncio
import stripe
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agents.stripe_handler import process_checkout_session
from agents.apollo_agent import fetch_leads
from agents.instantly_agent import push_leads_to_campaign
from agents.arcads_agent import generate_ugc_video

app = FastAPI(title="Dual Agency UGC Engine")

STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

async def execute_autonomous_workflow(order_data: dict):
    """
    Executes the Dual-Agency pipeline based on tier in an async context.
    """
    amount = order_data.get("amount", 0)
    customer_email = order_data.get("customer_email")
    print(f"[Engine] Triggering pipeline for {customer_email} (${amount})")

    # Tier 1 & 2: Lead Acquisition + Email Campaign Injection
    if amount in [150, 500]:
        leads = fetch_leads(limit=25 if amount == 150 else 100)
        if leads:
            push_leads_to_campaign(campaign_id="default_campaign_id", leads=leads)

    # Tier 3 & 4: UGC Video Generation Pipeline
    elif amount in [1000, 2500]:
        script = f"Transform your business with top tier UGC ads. Designed for {customer_email}."
        await generate_ugc_video(script_text=script)

@app.get("/")
def health_check():
    return {"status": "online", "agency": "Dual UGC Autonomous Infrastructure"}

@app.post("/webhook/stripe")
async def stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    sig_header = request.headers.get("stripe-signature")
    payload = await request.body()

    # If signature header is present AND secret is set, verify with Stripe
    if sig_header and STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Webhook signature error: {str(e)}")
    else:
        # Fall back to raw JSON parsing for testing
        event = await request.json()

    if event.get("type") == "checkout.session.completed":
        session = event["data"]["object"]
        order = process_checkout_session(session)
        background_tasks.add_task(execute_autonomous_workflow, order)

    return {"status": "success", "message": "Event processed successfully"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)

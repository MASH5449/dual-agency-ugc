import os
import stripe

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

def process_checkout_session(session: dict):
    """
    Parses completed Stripe checkout sessions and extracts order metadata.
    """
    customer_email = session.get("customer_details", {}).get("email")
    amount_total = session.get("amount_total", 0) / 100  # Convert cents to dollars
    
    order_data = {
        "customer_email": customer_email,
        "amount": amount_total,
        "session_id": session.get("id"),
        "status": session.get("payment_status")
    }
    
    print(f"[Stripe Agent] Processed payment of ${amount_total} from {customer_email}")
    return order_data

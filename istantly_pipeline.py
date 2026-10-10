import os
import requests

INSTANTLY_API_KEY = os.getenv("INSTANTLY_API_KEY")
INSTANTLY_CAMPAIGN_ID = os.getenv("INSTANTLY_CAMPAIGN_ID")


def push_lead_to_instantly(email: str, video_url: str):
    """
    Pushes customer email and finished HeyGen video link to Instantly.ai sequence.
    """
    if not INSTANTLY_API_KEY or not INSTANTLY_CAMPAIGN_ID:
        print("[Instantly] Missing INSTANTLY_API_KEY or INSTANTLY_CAMPAIGN_ID environment variables.")
        return {"status": "error", "message": "Credentials missing"}

    url = "https://api.instantly.ai/api/v1/lead/add"
    payload = {
        "api_key": INSTANTLY_API_KEY,
        "campaign_id": INSTANTLY_CAMPAIGN_ID,
        "email": email,
        "custom_variables": {
            "video_url": video_url
        },
        "skip_if_in_workspace": False
    }

    try:
        response = requests.post(url, json=payload, timeout=15)
        print(f"[Instantly SUCCESS] Lead {email} added to campaign {INSTANTLY_CAMPAIGN_ID}")
        return response.json()
    except Exception as e:
        print(f"[Instantly Exception] Failed to add lead: {e}")
        return {"status": "error", "message": str(e)}

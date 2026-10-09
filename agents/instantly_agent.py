import os
import requests

INSTANTLY_API_KEY = os.getenv("INSTANTLY_API_KEY")

def push_leads_to_campaign(campaign_id: str, leads: list):
    """
    Pushes Apollo leads directly into an Instantly outreach campaign using API v2.
    """
    if not INSTANTLY_API_KEY:
        print("[Instantly Agent] Error: INSTANTLY_API_KEY is not configured.")
        return False

    url = "https://api.instantly.ai/api/v2/leads"
    headers = {
        "Authorization": f"Bearer {INSTANTLY_API_KEY}",
        "Content-Type": "application/json"
    }

    formatted_leads = []
    for lead in leads:
        formatted_leads.append({
            "email": lead.get("email"),
            "first_name": lead.get("first_name", ""),
            "last_name": lead.get("last_name", ""),
            "company_name": lead.get("organization", {}).get("name", ""),
            "custom_variables": {
                "title": lead.get("title", "")
            }
        })

    payload = {
        "campaign_id": campaign_id,
        "leads": formatted_leads,
        "skip_if_in_workspace": True
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        print(f"[Instantly Agent] Successfully pushed {len(formatted_leads)} leads to campaign {campaign_id}.")
        return True
    except Exception as e:
        print(f"[Instantly Agent] API call failed: {e}")
        return False

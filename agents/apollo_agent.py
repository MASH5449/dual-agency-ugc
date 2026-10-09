import os
import requests

APOLLO_API_KEY = os.getenv("APOLLO_API_KEY")

def fetch_leads(job_titles=None, location="United States", limit=10):
    """
    Retrieves targeted leads from Apollo API based on search parameters.
    """
    if not APOLLO_API_KEY:
        print("[Apollo Agent] Error: APOLLO_API_KEY is not set.")
        return []

    url = "https://api.apollo.io/v1/mixed_people/search"
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache"
    }
    
    payload = {
        "api_key": APOLLO_API_KEY,
        "person_titles": job_titles or ["Founder", "CEO", "Marketing Director"],
        "person_locations": [location],
        "page": 1,
        "per_page": limit
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        leads = data.get("people", [])
        print(f"[Apollo Agent] Successfully fetched {len(leads)} leads.")
        return leads
    except Exception as e:
        print(f"[Apollo Agent] API Call failed: {e}")
        return []

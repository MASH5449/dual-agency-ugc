import os
import requests

APOLLO_API_KEY = os.getenv("APOLLO_API_KEY")

def fetch_leads(limit: int = 25):
    """
    Fetches leads from Apollo API using header-based authentication.
    """
    if not APOLLO_API_KEY:
        print("[Apollo Agent] Error: APOLLO_API_KEY environment variable missing.")
        return []

    url = "https://api.apollo.io/v1/mixed_people/search"
    
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "api-key": APOLLO_API_KEY
    }

    payload = {
        "page": 1,
        "per_page": limit,
        "person_titles": ["Founder", "CEO", "Owner"]
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        people = data.get("people", [])
        print(f"[Apollo Agent] Successfully fetched {len(people)} leads.")
        return people
    except Exception as e:
        print(f"[Apollo Agent] API Call failed: {e}")
        return []

import os
import requests

APOLLO_API_KEY = os.getenv("APOLLO_API_KEY")

def fetch_leads(limit: int = 25):
    """
    Fetches live leads from Apollo API using header authentication.
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
        "api_key": APOLLO_API_KEY,
        "page": 1,
        "per_page": limit,
        "person_titles": ["Founder", "CEO", "Owner"]
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        people = data.get("people", [])
        print(f"[Apollo Agent] Successfully fetched {len(people)} live leads from Apollo.")
        return people
    except requests.exceptions.HTTPError as err:
        print(f"[Apollo Agent] API Call failed with status {err.response.status_code}")
        if err.response is not None:
            print(f"[Apollo Agent] Response Body: {err.response.text}")
        return []
    except Exception as e:
        print(f"[Apollo Agent] Unexpected Error: {e}")
        return []

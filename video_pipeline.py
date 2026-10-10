import os
import requests

HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY")
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"


def get_public_avatar_look():
    """
    Fetches an active public avatar look_id and voice_id dynamically
    from HeyGen v3 to guarantee valid IDs.
    """
    url = "https://api.heygen.com/v3/avatars/looks?ownership=public&limit=1"
    headers = {"x-api-key": HEYGEN_API_KEY}
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            looks = res.json().get("data", [])
            if looks:
                look = looks[0]
                return look.get("id"), look.get("default_voice_id")
    except Exception as e:
        print(f"[HeyGen Look Lookup Warning] Could not fetch look ID: {e}")
        
    return None, None


def generate_video(product_url: str, tier: str):
    """
    Automated HeyGen v3 UGC Pipeline.
    Receives product_url from Stripe webhook and initiates avatar video rendering.
    """
    if MOCK_MODE or not HEYGEN_API_KEY:
        print(f"[Video Pipeline - MOCK] Logging order for: {product_url} ({tier})")
        return {
            "status": "success",
            "message": "Mock mode active",
            "product_url": product_url
        }

    # Automatically grab a valid Look ID from your HeyGen workspace
    avatar_id, default_voice = get_public_avatar_look()
    voice_id = default_voice or "2d5b0e6cf36f460aa7fc47e3eee4ba54"

    if not avatar_id:
        print("[HeyGen API Error] Failed to retrieve a valid public avatar look_id.")
        return {"status": "error", "message": "No valid avatar look found."}

    url = "https://api.heygen.com/v3/videos"
    headers = {
        "x-api-key": HEYGEN_API_KEY,
        "Content-Type": "application/json"
    }

    script_text = (
        f"Hey there! If you haven't checked out {product_url} yet, you are seriously missing out. "
        f"This is easily one of our top picks this season. Go grab yours today!"
    )

    payload = {
        "type": "avatar",
        "avatar_id": avatar_id,
        "script": script_text,
        "voice_id": voice_id,
        "aspect_ratio": "9:16"
    }

    try:
        print(f"[HeyGen v3 Pipeline] Triggering video generation for: {product_url} ({tier} tier) with avatar_id: {avatar_id}")
        response = requests.post(url, json=payload, headers=headers, timeout=30)

        if response.status_code in [200, 201]:
            data = response.json()
            video_id = data.get("data", {}).get("video_id") or data.get("data", {}).get("id", "N/A")
            print(f"[HeyGen Pipeline SUCCESS] Render job created! Video ID: {video_id}")
            return data
        else:
            print(f"[HeyGen API Error] Status {response.status_code}: {response.text}")
            return {"status": "error", "details": response.text}

    except Exception as e:
        print(f"[HeyGen Exception] Connection failed: {e}")
        return {"status": "error", "message": str(e)}

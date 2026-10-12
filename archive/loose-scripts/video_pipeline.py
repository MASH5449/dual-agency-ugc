import os
import requests

HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY")
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"


FALLBACK_VOICE_ID = "2d5b0e6cf36f460aa7fc47e3eee4ba54"


def get_public_avatar_looks(limit: int = 10):
    """
    Fetches up to `limit` active public avatar looks from HeyGen v3 as a list
    of (look_id, voice_id) candidates, so rendering can fall back if one is
    unavailable. Returns an empty list on failure.
    """
    url = f"https://api.heygen.com/v3/avatars/looks?ownership=public&limit={limit}"
    headers = {"x-api-key": HEYGEN_API_KEY}

    candidates = []
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            for look in res.json().get("data", []) or []:
                look = look or {}
                look_id = look.get("id") or look.get("look_id")
                if look_id:
                    candidates.append(
                        (look_id, look.get("default_voice_id") or FALLBACK_VOICE_ID)
                    )
    except Exception as e:
        print(f"[HeyGen Look Lookup Warning] Could not fetch look ID: {e}")

    return candidates


def get_public_avatar_look():
    """Backwards-compatible: the first public look as (look_id, voice_id)."""
    candidates = get_public_avatar_looks(limit=1)
    if candidates:
        return candidates[0]
    return None, None


def _is_avatar_not_found(response) -> bool:
    """True for an HTTP 404 whose JSON body has the code avatar_not_found."""
    if response.status_code != 404:
        return False
    try:
        body = response.json()
    except ValueError:
        return "avatar_not_found" in (response.text or "")
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return error.get("code") == "avatar_not_found"
    if isinstance(error, str):
        return error == "avatar_not_found"
    return isinstance(body, dict) and body.get("code") == "avatar_not_found"


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
    candidates = get_public_avatar_looks()

    if not candidates:
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

    attempted = []
    try:
        for avatar_id, voice_id in candidates:
            payload = {
                "type": "avatar",
                "avatar_id": avatar_id,
                "script": script_text,
                "voice_id": voice_id,
                "aspect_ratio": "9:16"
            }
            print(f"[HeyGen v3 Pipeline] Triggering video generation for: {product_url} ({tier} tier) with avatar_id: {avatar_id}")
            response = requests.post(url, json=payload, headers=headers, timeout=30)

            if response.status_code in [200, 201]:
                data = response.json()
                video_id = data.get("data", {}).get("video_id") or data.get("data", {}).get("id", "N/A")
                print(f"[HeyGen Pipeline SUCCESS] Render job created with avatar_id {avatar_id}! Video ID: {video_id}")
                return data

            if _is_avatar_not_found(response):
                print(f"[HeyGen API Warning] avatar_not_found for avatar_id {avatar_id}; trying next public avatar")
                attempted.append(avatar_id)
                continue

            print(f"[HeyGen API Error] Status {response.status_code}: {response.text}")
            return {"status": "error", "details": response.text}

        message = f"avatar_not_found for all {len(attempted)} public avatar candidate(s) tried: {', '.join(attempted)}"
        print(f"[HeyGen API Error] {message}")
        return {"status": "error", "message": message}

    except Exception as e:
        print(f"[HeyGen Exception] Connection failed: {e}")
        return {"status": "error", "message": str(e)}

import os
import requests

HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY")
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"


def generate_video(product_url: str, tier: str):
    """
    Automated HeyGen UGC pipeline.
    Receives product_url from Stripe checkout and triggers HeyGen avatar rendering.
    """
    if MOCK_MODE or not HEYGEN_API_KEY:
        print(f"[Video Pipeline - MOCK] Logging order for: {product_url} ({tier})")
        return {
            "status": "success",
            "message": "Mock mode active",
            "product_url": product_url
        }

    url = "https://api.heygen.com/v2/video/generate"
    headers = {
        "X-Api-Key": HEYGEN_API_KEY,
        "Content-Type": "application/json"
    }

    # Custom UGC script dynamically referencing their site
    script_text = (
        f"Hey there! If you haven't checked out {product_url} yet, you are seriously missing out. "
        f"This is easily one of our top picks this season. Go grab yours today!"
    )

    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": "Daisy-inic-20220818",  # Default UGC Avatar ID
                    "avatar_style": "normal"
                },
                "voice": {
                    "type": "text",
                    "input_text": script_text,
                    "voice_id": "1bd001e7e50f421d891986aad5158bc6"
                }
            }
        ],
        "dimension": {
            "width": 1080,
            "height": 1920  # 9:16 Vertical format for Reels/TikTok
        }
    }

    try:
        print(f"[HeyGen Pipeline] Directing HeyGen API to render UGC video for: {product_url}")
        response = requests.post(url, json=payload, headers=headers, timeout=30)

        if response.status_code in [200, 201]:
            data = response.json()
            video_id = data.get("data", {}).get("video_id", "N/A")
            print(f"[HeyGen Pipeline SUCCESS] Render job initiated! Video ID: {video_id}")
            return data
        else:
            print(f"[HeyGen API Error] Status {response.status_code}: {response.text}")
            return {"status": "error", "details": response.text}

    except Exception as e:
        print(f"[HeyGen Exception] Connection failed: {e}")
        return {"status": "error", "message": str(e)}

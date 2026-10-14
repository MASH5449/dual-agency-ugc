import json


HEYGEN_VIDEO_URL = "https://api.heygen.com/v3/videos"


def submit_heygen_video(script, avatar_id, voice_id, api_key, post):
    response = post(
        HEYGEN_VIDEO_URL,
        json={
            "type": "avatar",
            "avatar_id": avatar_id,
            "script": script,
            "voice_id": voice_id,
            "aspect_ratio": "9:16",
        },
        headers={
            "X-Api-Key": api_key,
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    if 400 <= response.status_code < 500:
        return {"status": "failed", "detail": f"HeyGen rejected the render (HTTP {response.status_code})."}
    if response.status_code < 200 or response.status_code >= 300:
        return {"status": "submission_unknown", "detail": "HeyGen did not confirm whether the render was accepted."}
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return {"status": "submission_unknown", "detail": "HeyGen accepted the request but returned an unreadable job response."}
    data = payload.get("data") if isinstance(payload, dict) else None
    video_id = data.get("video_id") or data.get("id") if isinstance(data, dict) else None
    if not isinstance(video_id, str) or not video_id.strip():
        return {"status": "submission_unknown", "detail": "HeyGen may have accepted the render, but did not return a job ID."}
    return {"status": "queued", "video_id": video_id.strip(), "detail": ""}

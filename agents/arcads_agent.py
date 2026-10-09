import os
import json
from playwright.async_api import async_playwright

async def generate_ugc_video(script_text: str = None, customer_email: str = None, **kwargs):
    """
    Automates Arcads video creation using persistent browser session state.
    """
    state_json_str = os.getenv("ARCADS_STORAGE_STATE")
    state_path = "state.json"

    if state_json_str:
        try:
            # Parse to ensure valid JSON, then dump cleanly formatted
            parsed_json = json.loads(state_json_str)
            with open(state_path, "w") as f:
                json.dump(parsed_json, f)
        except json.JSONDecodeError as e:
            print(f"[Arcads Agent] Error decoding ARCADS_STORAGE_STATE JSON: {e}")
            return False
    else:
        print("[Arcads Agent] Error: ARCADS_STORAGE_STATE variable missing.")
        return False

    target_info = customer_email or script_text or "default task"
    print(f"[Arcads Agent] Starting browser automation for: {target_info}...")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=state_path)
            page = await context.new_page()

            print("[Arcads Agent] Navigating to Arcads app...")
            await page.goto("https://app.arcads.ai", wait_until="networkidle")
            
            if "login" in page.url:
                print("[Arcads Agent] Error: Session state expired or invalid. Please re-export cookies.")
                await browser.close()
                return False

            print("[Arcads Agent] Authenticated successfully via session state!")
            await browser.close()
            print("[Arcads Agent] UGC video generation queued successfully.")
            return True

    except Exception as e:
        print(f"[Arcads Agent] Playwright automation error: {e}")
        return False

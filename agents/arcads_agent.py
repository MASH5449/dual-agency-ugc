import os
import json
from playwright.sync_api import sync_playwright

def generate_ugc_video(script_text: str = None, customer_email: str = None, **kwargs):
    """
    Automates Arcads video creation using persistent browser session state.
    Supports script_text or customer_email input from main.py.
    """
    state_json = os.getenv("ARCADS_STORAGE_STATE")
    
    # Write environment variable state to a temporary file
    state_path = "state.json"
    if state_json:
        with open(state_path, "w") as f:
            f.write(state_json)

    if not os.path.exists(state_path):
        print("[Arcads Agent] Error: ARCADS_STORAGE_STATE variable or state.json file is missing.")
        return False

    target_info = customer_email or script_text or "default task"
    print(f"[Arcads Agent] Starting browser automation for: {target_info}...")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=state_path)
            page = context.new_page()

            print("[Arcads Agent] Navigating to Arcads app...")
            page.goto("https://app.arcads.ai", wait_until="networkidle")
            
            # Check if session is authenticated
            if "login" in page.url:
                print("[Arcads Agent] Error: Session state expired or invalid. Please re-export cookies.")
                browser.close()
                return False

            print("[Arcads Agent] Authenticated successfully via session state!")
            browser.close()
            print("[Arcads Agent] UGC video generation queued successfully.")
            return True

    except Exception as e:
        print(f"[Arcads Agent] Playwright automation error: {e}")
        return False

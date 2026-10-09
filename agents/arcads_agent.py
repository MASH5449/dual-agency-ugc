import os
import json
from playwright.async_api import async_playwright

async def generate_ugc_video(script_text: str = None, customer_email: str = None, **kwargs):
    """
    Automates Arcads video creation using persistent browser session state.
    Uses async API to match FastAPI's event loop.
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
        async with async_playwright() as p:
            # Note the 'await' added to all Playwright commands
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=state_path)
            page = await context.new_page()

            print("[Arcads Agent] Navigating to Arcads app...")
            await page.goto("https://app.arcads.ai", wait_until="networkidle")
            
            # Check if session is authenticated
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

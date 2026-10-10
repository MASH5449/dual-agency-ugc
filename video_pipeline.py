import os
import requests
import json
from bs4 import BeautifulSoup
from openai import OpenAI

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

ARCADS_CLIENT_ID = os.getenv("ARCADS_CLIENT_ID")
ARCADS_CLIENT_SECRET = os.getenv("ARCADS_CLIENT_SECRET")

def scrape_broll_images(product_url: str):
    """Scrapes product image assets from product_url to use as visual B-roll cuts."""
    images = []
    try:
        response = requests.get(product_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-src")
                if src and (src.endswith(".jpg") or src.endswith(".png") or src.endswith(".webp")):
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = product_url.rstrip("/") + src
                    images.append(src)
    except Exception as e:
        print(f"[Scraper] Error scraping {product_url}: {e}")
    return images[:5]

def trigger_arcads_render(script: str, b_roll_images: list = None):
    """Calls Arcads API using HTTP Basic Auth credentials."""
    url = "https://api.arcads.ai/v1/renders"
    auth = (ARCADS_CLIENT_ID, ARCADS_CLIENT_SECRET) if ARCADS_CLIENT_ID and ARCADS_CLIENT_SECRET else None
    
    payload = {
        "script": script,
        "b_roll_images": b_roll_images or []
    }
    
    try:
        response = requests.post(url, json=payload, auth=auth, timeout=15)
        print(f"[Arcads Engine] Render triggered. HTTP Status: {response.status_code}")
        return response.status_code == 200
    except Exception as e:
        print(f"[Arcads Engine] API Render Error: {e}")
        return False

def generate_video(product_url: str, tier: str):
    """
    Tier Execution Matrix:
    - 'quick'     ($150): 1 script (30s) -> 1 render
    - 'scale'     ($500): 3 core scripts + 5 hooks -> 15 renders + Scraped B-roll
    - 'whitelabel' ($2,500): 12 scripts x 5 hooks -> 60 batch renders
    """
    print(f"[Video Pipeline] Executing '{tier}' tier for product URL: {product_url}")

    if tier == 'quick':
        prompt = f"Write 1 high-converting 30-second UGC video ad script for a product at {product_url}. Return ONLY the script text."
        res = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        script = res.choices[0].message.content
        trigger_arcads_render(script)

    elif tier == 'scale':
        broll = scrape_broll_images(product_url)
        prompt = f"Create 3 core UGC ad scripts (20s each) and 5 catchy opening hook scripts (3s each) for product at {product_url}. Return valid JSON with keys 'scripts' (list of 3 strings) and 'hooks' (list of 5 strings)."
        
        res = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}]
        )
        data = json.loads(res.choices[0].message.content)
        
        for script in data.get("scripts", []):
            for hook in data.get("hooks", []):
                full_script = f"{hook}\n\n{script}"
                trigger_arcads_render(full_script, b_roll_images=broll)

    elif tier == 'whitelabel':
        prompt = f"Create 12 core UGC ad scripts and 5 distinct opening hooks for agency brand at {product_url}. Return valid JSON with keys 'scripts' (12 strings) and 'hooks' (5 strings)."
        
        res = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}]
        )
        data = json.loads(res.choices[0].message.content)
        
        for script in data.get("scripts", []):
            for hook in data.get("hooks", []):
                full_script = f"{hook}\n\n{script}"
                trigger_arcads_render(full_script)

    return True

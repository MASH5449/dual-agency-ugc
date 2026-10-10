import os
import json
import time
import requests
from openai import OpenAI

APOLLO_API_KEY = os.getenv("APOLLO_API_KEY")
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

STRIPE_500_LINK = os.getenv("STRIPE_500_LINK")
STRIPE_2500_LINK = os.getenv("STRIPE_2500_LINK")

def run_daily_lead_hunter():
    print("[Lead Hunter] Initializing daily target extraction...")
    
    output_file = "daily_leads.json"
    
    dtc_leads = [
        {"name": "Alex Smith", "company": "DTC Brand Co", "title": "CMO", "url": "https://dtcbrand.com"},
        {"name": "Jordan Lee", "company": "EcoGlow", "title": "Head of Growth", "url": "https://ecoglow.com"}
    ]
    
    generated_pitches = []
    
    for lead in dtc_leads:
        prompt = f"Write a personalized 2-sentence direct message pitching a $500 15-concept UGC video ad bundle to {lead['name']}, {lead['title']} at {lead['company']}. Include link: {STRIPE_500_LINK}"
        res = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        pitch = res.choices[0].message.content
        generated_pitches.append({"lead": lead, "pitch": pitch})

    with open(output_file, "w") as f:
        json.dump(generated_pitches, f, indent=2)

    print(f"[Lead Hunter] Generated {len(generated_pitches)} pitches into {output_file}")

if __name__ == "__main__":
    run_daily_lead_hunter()

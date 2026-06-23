import imaplib
import email
import os
import datetime
from email.header import decode_header
from pathlib import Path

import re

import re
import requests
import json
import imaplib
import email
import os
import datetime
from pathlib import Path

# --- CONFIGURATION ---
IMAP_SERVER = "imap.gmail.com"
EMAIL_USER = "bilyky@gmail.com"
EMAIL_PASS = os.environ.get("SMTP_PASSWORD")
BASE_DIR = Path(__file__).resolve().parent

# Keywords that signal a stock-oriented email
STOCK_KEYWORDS = ["BUY", "SELL", "STOCK", "TICKER", "NEWSLETTER", "PICK", "ALPHA", "PORTFOLIO", "EARNINGS"]

def get_ai_reasoning(symbol, industry, pgr, s10, l60):
    """Call the GitHub Models API (gpt-4o-mini) to generate a live, ruthless risk audit."""
    token = None
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.startswith("GITHUB_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip('"')

    if not token:
        # Fallback to local heuristics if token is missing
        return f"<b>Standard Audit:</b> Technicals and Fundamentals aligned ({pgr})."

    url = "https://models.inference.ai.azure.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""
    Analyze stock {symbol} (Industry: {industry}, Chaikin Rating: {pgr}, Short10 Score: {s10}, Long60 Score: {l60}).
    Generate a 2-sentence analysis:
    Sentence 1: (Devil's Advocate) Ruthlessly criticize why this trade might fail. Be highly skeptical.
    Sentence 2: (Strategic Catalyst) Identify the 'Strategic Requirement' or 'AI Force Multiplier' if it exists, or the primary momentum driver.
    Keep the output extremely tight, professional, and under 50 words. Do not use prefixes like 'Devil's Advocate:'.
    """

    data = {
        "messages": [
            {"role": "system", "content": "You are Project AETHER, an elite, highly skeptical hedge fund risk manager. Your tone is professional, concise, and critical."},
            {"role": "user", "content": prompt}
        ],
        "model": "gpt-4o-mini",
        "max_tokens": 100,
        "temperature": 0.3
    }

    try:
        r = requests.post(url, json=data, headers=headers)
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"].strip()
            # Split sentences for formatting
            sentences = content.split(". ")
            devils_advocate = sentences[0] + "." if len(sentences) > 0 else ""
            catalyst = ". ".join(sentences[1:]) if len(sentences) > 1 else ""
            
            return f"🚨 <b>Devil's Advocate:</b> {devils_advocate}<br>💡 <b>Catalyst:</b> {catalyst}"
        else:
            return f"<b>API Error ({r.status_code}):</b> Fallback to technical alignment."
    except Exception as e:
        return f"<b>Verification failed:</b> {e}"

def extract_tickers(text):
    """Regex to find likely stock tickers (2-5 uppercase letters)."""
    # Matches $ABC or standalone ABC in a financial context
    found = re.findall(r'\$?([A-Z]{2,5})\b', text)
    return list(set(found))

def fetch_idea_emails():
    """Check inbox for all stock-oriented emails from the last 24h."""
    if not EMAIL_PASS:
        print("Error: SMTP_PASSWORD not set. Cannot check emails.")
        return []

    ideas = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")

        # Search for emails from the last 24h
        date = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%d-%b-%Y")
        
        # Broad search: Check all emails since yesterday
        status, messages = mail.search(None, f'(SINCE "{date}")')

        if status == "OK":
            for num in messages[0].split():
                status, data = mail.fetch(num, "(RFC822)")
                if status == "OK":
                    raw_email = data[0][1]
                    msg = email.message_from_bytes(raw_email)
                    
                    subject = str(msg["subject"] or "").upper()
                    sender = str(msg["from"] or "").lower()
                    
                    # 1. Filter: Does the subject or body look stock-oriented?
                    # 2. Extract Body
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode(errors='ignore')
                                break
                    else:
                        body = msg.get_payload(decode=True).decode(errors='ignore')
                    
                    # 3. Decision Logic: Is this high-signal?
                    content_to_check = (subject + " " + body).upper()
                    if any(k in content_to_check for k in STOCK_KEYWORDS) or "$" in content_to_check:
                        tickers = extract_tickers(content_to_check)
                        ideas.append({
                            "subject": msg["subject"],
                            "from": msg["from"],
                            "body": body[:500].strip() + "...", # Truncate for report
                            "tickers": tickers
                        })

        mail.logout()
    except Exception as e:
        print(f"Failed to fetch emails: {e}")
    
    return ideas

def get_market_news(symbols):
    """Fetch top news summaries for specific symbols or sectors."""
    # This will be integrated with the system's search tool capabilities
    # For the autonomous script, we'll log that news needs to be pulled.
    print(f"Hiring AETHER news bots to scan for: {', '.join(symbols)}")
    return []

if __name__ == "__main__":
    # Test
    print("Checking for new AETHER ideas...")
    new_ideas = fetch_idea_emails()
    for idea in new_ideas:
        print(f"💡 Idea from {idea['from']}: {idea['body']}")

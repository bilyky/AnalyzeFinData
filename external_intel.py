import imaplib
import email
import os
import datetime
from email.header import decode_header
from pathlib import Path

import re

# --- CONFIGURATION ---
IMAP_SERVER = "imap.gmail.com"
EMAIL_USER = "bilyky@gmail.com"
EMAIL_PASS = os.environ.get("SMTP_PASSWORD")

# Keywords that signal a stock-oriented email
STOCK_KEYWORDS = ["BUY", "SELL", "STOCK", "TICKER", "NEWSLETTER", "PICK", "ALPHA", "PORTFOLIO", "EARNINGS"]

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

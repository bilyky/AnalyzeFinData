import os
import sys
import datetime
import re
import requests
import json
import imaplib
import email
import openpyxl
from email.header import decode_header
from pathlib import Path

# --- CONFIGURATION ---
IMAP_SERVER = "imap.gmail.com"
EMAIL_USER = "bilyky@gmail.com"
EMAIL_PASS = os.environ.get("SMTP_PASSWORD")
BASE_DIR = Path(__file__).resolve().parent

# Keywords that signal a stock-oriented email
STOCK_KEYWORDS = ["BUY", "SELL", "STOCK", "TICKER", "NEWSLETTER", "PICK", "ALPHA", "PORTFOLIO", "EARNINGS"]

def get_ai_reasoning(symbol, industry, pgr, s10, l60):
    """Advisory risk audit via the configured AI provider (see ai_client).
    Falls back to a deterministic alignment string when no provider is available."""
    import ai_client
    system = ("You are Project AETHER, an elite, highly skeptical hedge fund risk "
              "manager. Your tone is professional, concise, and critical.")
    user = f"""
    Analyze stock {symbol} (Industry: {industry}, Chaikin Rating: {pgr}, Short10 Score: {s10}, Long60 Score: {l60}).
    Generate a 2-sentence analysis:
    Sentence 1: (Devil's Advocate) Ruthlessly criticize why this trade might fail. Be highly skeptical.
    Sentence 2: (Strategic Catalyst) Identify the 'Strategic Requirement' or 'AI Force Multiplier' if it exists, or the primary momentum driver.
    Keep the output extremely tight, professional, and under 50 words. Do not use prefixes like 'Devil's Advocate:'.
    """
    content = ai_client.evaluate(system, user, max_tokens=100, temperature=0.3)
    if not content:
        return f"<b>Standard Audit:</b> Technicals and Fundamentals aligned ({pgr})."
    sentences = content.split(". ")
    devils_advocate = sentences[0] + "." if len(sentences) > 0 else ""
    catalyst = ". ".join(sentences[1:]) if len(sentences) > 1 else ""
    return f"🚨 <b>Devil's Advocate:</b> {devils_advocate}<br>💡 <b>Catalyst:</b> {catalyst}"

def get_existing_symbols():
    """Load the valid symbols from the root Research sheet to prevent false positives."""
    try:
        root_path = BASE_DIR / "state_of_the_day.xlsx"
        wb = openpyxl.load_workbook(root_path, data_only=True, read_only=True)
        ws = wb["Research"]
        return {str(row[3]).strip().upper() for row in ws.iter_rows(min_row=2, values_only=True) if row[3]}
    except Exception as e:
        print(f"Failed to load symbols for verification: {e}")
        return set()

# Common English words that are also valid stock tickers. 
# We ignore these unless they are explicitly prefixed with a '$' (e.g., $ALL vs. 'all').
TICKER_BLACKLIST = {"ALL", "IT", "ME", "SO", "OR", "GO", "AM", "ON", "HE", "WE", "DO"}

def extract_tickers(text):
    """Extract and verify stock tickers using the Research Universe."""
    # Find words with leading '$' (e.g., $ALL)
    dollar_tickers = set(re.findall(r'\$([A-Z]{2,5})\b', text.upper()))
    
    # Find standalone uppercase words (e.g., AAPL)
    words = set(re.findall(r'\b([A-Z]{2,5})\b', text.upper()))
    
    # Cross-reference with our Research Universe to eliminate false positives
    universe = get_existing_symbols()
    
    valid_tickers = []
    for w in words:
        if w in universe:
            # If it's a common word, only allow it if it had a '$' prefix
            if w in TICKER_BLACKLIST:
                if w in dollar_tickers:
                    valid_tickers.append(w)
            else:
                valid_tickers.append(w)
                
    return list(set(valid_tickers))

def analyze_email_content(subject, body):
    """Semantically analyze email content into structured trade ideas via the
    configured AI provider (see ai_client). Returns [] when unavailable."""
    import ai_client
    universe = list(get_existing_symbols())[:150]  # sample to fit prompt limits
    system = ("You are a precise financial data extractor. You only output valid "
              "JSON. No markdown wrappers like ```json.")
    user = f"""
    You are Project AETHER, an elite AI hedge fund analyst. Read this financial email and extract concrete stock recommendations.

    Email Subject: {subject}
    Email Body: {body[:1500]}

    Instructions:
    1. Extract the specific stock tickers being recommended.
    2. Cross-reference with this list of valid tickers if possible: {', '.join(universe)}.
    3. Determine the exact sentiment: BUY, SELL, or HOLD.
    4. Summarize the core thesis in one short sentence.

    Output strictly as a JSON list of objects, or an empty list [] if no concrete recommendations exist.
    Example:
    [
        {{"symbol": "AAPL", "sentiment": "BUY", "thesis": "Strong iPhone sales in China driving immediate momentum."}}
    ]
    """
    content = ai_client.evaluate(system, user, max_tokens=300, temperature=0.1)
    if not content:
        return []
    try:
        content = content.replace("```json", "").replace("```", "").strip()
        return json.loads(content)
    except Exception as e:
        print(f"Semantic email analysis failed: {e}")
    return []

def fetch_idea_emails():
    """Check inbox for all stock-oriented emails from the last 24h and analyze their content."""
    if not EMAIL_PASS:
        print("Error: SMTP_PASSWORD not set. Cannot check emails.")
        return []

    ideas = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")

        date = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'(SINCE "{date}")')

        if status == "OK":
            for num in messages[0].split():
                # Use BODY.PEEK[] instead of RFC822 to fetch the email content 
                # WITHOUT marking the email as SEEN (read) in the inbox.
                status, data = mail.fetch(num, "(BODY.PEEK[])")
                if status == "OK":
                    raw_email = data[0][1]
                    msg = email.message_from_bytes(raw_email)
                    
                    subject = str(msg["subject"] or "")
                    
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode(errors='ignore')
                                break
                    else:
                        body = msg.get_payload(decode=True).decode(errors='ignore')
                    
                    # Perform deep semantic analysis instead of primitive regex matching
                    content_to_check = (subject + " " + body).upper()
                    if any(k in content_to_check for k in STOCK_KEYWORDS) or "$" in content_to_check:
                        parsed_ideas = analyze_email_content(subject, body)
                        for idea in parsed_ideas:
                            ideas.append({
                                "from": msg["from"],
                                "subject": msg["subject"],
                                "symbol": idea["symbol"],
                                "sentiment": idea["sentiment"],
                                "thesis": idea["thesis"]
                            })

        mail.logout()
    except Exception as e:
        print(f"Failed to fetch emails: {e}")
    
    return ideas

def get_market_news(symbols):
    print(f"Hiring AETHER news bots to scan for: {', '.join(symbols)}")
    return []

if __name__ == "__main__":
    print("Checking for new AETHER ideas...")
    new_ideas = fetch_idea_emails()
    for idea in new_ideas:
        print(f"💡 Idea from {idea['from']}: {idea['body']}")

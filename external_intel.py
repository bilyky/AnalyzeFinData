import os
import datetime
import re
import json
import imaplib
import email
import extract_email_intel
import openpyxl
import ai_client
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from config import CFG
from aether_logger import get_logger as _get_logger

_log = _get_logger("external_intel")

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent

# Keywords that signal a stock-oriented email
STOCK_KEYWORDS = ["BUY", "SELL", "STOCK", "TICKER", "NEWSLETTER", "PICK", "ALPHA", "PORTFOLIO", "EARNINGS"]

def get_ai_reasoning(symbol, industry, pgr, s10, l60):
    """Advisory risk audit via the configured AI provider (see ai_client).
    Falls back to a deterministic alignment string when no provider is available."""
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
        _log.error(f"Failed to load symbols for verification: {e}")
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
        _log.error(f"Semantic email analysis failed: {e}")
    return []

def _max_intel_emails() -> int:
    try:
        return CFG.ai_max_intel_emails
    except Exception:
        return 20


def fetch_idea_emails():
    """Check inbox, Promotions, and Trash for stock-oriented emails from the last 24h.
    Supports scanning multiple mailboxes defined in config.json.
    Returns standard ticker ideas (analyze_email_content) AND structural intel
    (extract_email_intel.extract) per email as 'intel' key."""
    # Gmail IMAP folder names. Spam is deliberately excluded — financial newsletters
    # rarely land there legitimately and it adds noise.
    FOLDERS = [
        "INBOX",
        "[Gmail]/Promotions",
        "[Gmail]/Trash",
    ]

    ideas = []
    processed_msg_ids = set()
    max_intel = _max_intel_emails()
    candidates = []

    for mb in CFG.mailboxes:
        email_user = mb["email"]
        if "example.com" in email_user.lower():
            _log.console(f"Skipping placeholder mailbox: {email_user}")
            continue
        pass_env = mb["password_env"]
        imap_server = mb["imap_server"]
        email_pass = os.environ.get(pass_env) or CFG.smtp_password

        if not email_pass:
            raise ValueError(f"No password for mailbox '{email_user}' "
                             f"(checked env var '{pass_env}' and CFG.smtp_password)")

        _log.console(f"Scanning mailbox: {email_user} on {imap_server}...")
        mail = None
        try:
            mail = imaplib.IMAP4_SSL(imap_server)
            mail.login(email_user, email_pass)

            date = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%d-%b-%Y")

            connection_lost = False
            for folder in FOLDERS:
                if connection_lost:
                    break
                try:
                    status, _ = mail.select(f'"{folder}"', readonly=True)
                    if status != "OK":
                        continue
                except Exception as e:
                    err_msg = str(e).lower()
                    if any(x in err_msg for x in ["closed", "eof", "abort", "connection", "socket"]):
                        _log.error(f"IMAP connection lost on select: {e}")
                        break
                    continue

                status, messages = mail.search(None, f'(SINCE "{date}")')
                if status != "OK" or not messages[0].split():
                    continue

                # Process newest emails first to prioritize today's fresh newsletters
                for num in reversed(messages[0].split()):
                    if len(candidates) >= max_intel:
                        break
                    try:
                        status, data = mail.fetch(num, "(BODY.PEEK[])")
                        if status != "OK":
                            continue
                        raw_email = data[0][1]
                        msg = email.message_from_bytes(raw_email)

                        # Deduplicate identical emails across folders or mailboxes
                        msg_id = msg.get("Message-ID", "")
                        if msg_id:
                            if msg_id in processed_msg_ids:
                                continue
                            processed_msg_ids.add(msg_id)

                        subject = str(msg["subject"] or "")
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == "text/plain":
                                    body = part.get_payload(decode=True).decode(errors="ignore")
                                    break
                        else:
                            body = msg.get_payload(decode=True).decode(errors="ignore")

                        content_to_check = (subject + " " + body).upper()
                        if not (any(k in content_to_check for k in STOCK_KEYWORDS) or "$" in content_to_check):
                            continue

                        # Skip automated system alerts, invoices, and personal notices to preserve our AI budget
                        lower_subj = subject.lower()
                        if any(ph in lower_subj for ph in ["aether alert", "invoice", "receipt", "milestone", "shopper", "transaction"]):
                            continue

                        candidates.append({
                            "from": msg["from"],
                            "subject": subject,
                            "body": body,
                            "folder": folder
                        })
                    except Exception as e:
                        err_msg = str(e).lower()
                        if any(x in err_msg for x in ["closed", "eof", "abort", "connection", "socket"]):
                            _log.error(f"IMAP connection lost during message fetch: {e}")
                            connection_lost = True
                            break
                        _log.error(f"Failed to process message {num}: {e}")

                if len(candidates) >= max_intel:
                    _log.console(f"Intel candidate cap ({max_intel}) reached; breaking email scan to protect rate-limits.")
                    break
        except Exception as e:
            raise RuntimeError(f"Failed to fetch emails for {email_user}: {e}")
        finally:
            if mail:
                try:
                    mail.logout()
                except Exception:
                    pass

    # Now, execute AI extractions on these candidates in parallel!
    if candidates:
        _log.console(f"Running parallel AI extractions on {len(candidates)} filtered newsletters...")
        
        def run_extractions(cand):
            try:
                # 1. Standard ticker extraction (BUY/SELL/HOLD)
                parsed_ideas = analyze_email_content(cand["subject"], cand["body"])
                # 2. Structural intel extraction via AI
                intel = extract_email_intel.extract(cand["subject"], cand["body"])
                return {
                    "cand": cand,
                    "parsed_ideas": parsed_ideas,
                    "intel": intel
                }
            except Exception as e:
                _log.error(f"Extraction failed for {cand['subject'][:40]}: {e}")
                return None

        # Execute concurrently with up to 5 parallel threads
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(run_extractions, c) for c in candidates]
            for fut in as_completed(futures):
                res = fut.result()
                if not res:
                    continue
                cand = res["cand"]
                parsed_ideas = res["parsed_ideas"]
                intel = res["intel"]
                
                base = {"from": cand["from"], "subject": cand["subject"], "folder": cand["folder"], "intel": intel}
                if parsed_ideas:
                    for idea in parsed_ideas:
                        ideas.append({**base, "symbol": idea["symbol"],
                                      "sentiment": idea["sentiment"], "thesis": idea["thesis"]})
                elif intel:
                    ideas.append({**base, "symbol": None, "sentiment": None, "thesis": None})

    return ideas

def get_market_news(symbols):
    _log.console(f"get_market_news called for: {', '.join(symbols)}")
    return []

if __name__ == "__main__":
    _log.console("Fetching external intel ideas...")
    new_ideas = fetch_idea_emails()
    for idea in new_ideas:
        _log.console(f"Idea from {idea['from']}: {idea['subject']} (Symbol: {idea.get('symbol')}, Sentiment: {idea.get('sentiment')})")

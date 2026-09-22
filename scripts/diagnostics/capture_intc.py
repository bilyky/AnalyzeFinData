import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import console_safe
console_safe.install()

import imaplib
import email
import datetime
from email.header import decode_header
from pathlib import Path

from aether_logger import get_logger as _get_logger

_log = _get_logger("capture_intc")

try:
    from config import CFG
    _mailbox = CFG.mailboxes[0] if CFG.mailboxes else {}
    IMAP_SERVER = _mailbox.get("imap_server", "imap.gmail.com")
    EMAIL_USER = _mailbox.get("email", "")
    EMAIL_PASS = os.environ.get(_mailbox.get("password_env", "SMTP_PASSWORD"))
except Exception:
    IMAP_SERVER = "imap.gmail.com"
    EMAIL_USER = os.environ.get("SENDER_EMAIL", "")
    EMAIL_PASS = os.environ.get("SMTP_PASSWORD")

BASE_DIR = Path(__file__).resolve().parent

def capture_intc_emails():
    if not EMAIL_USER or not EMAIL_PASS:
        _log.error("[capture_intc] Email credentials not configured. Set mailboxes in config.json or SENDER_EMAIL/SMTP_PASSWORD env vars.")
        return
    
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")
        
        today_str = datetime.date.today().strftime("%d-%b-%Y")
        _log.console(f"Searching inbox for any email containing 'INTC' received today ({today_str})...")
        status, messages = mail.search(None, f'(SINCE "{today_str}")')
        
        found = False
        if status == "OK":
            for num in messages[0].split():
                status, data = mail.fetch(num, "(BODY.PEEK[])")
                if status == "OK":
                    raw_email = data[0][1]
                    msg = email.message_from_bytes(raw_email)
                    
                    subject = str(msg["subject"] or "")
                    sender = str(msg["from"] or "")
                    
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode(errors='ignore')
                                break
                    else:
                        body = msg.get_payload(decode=True).decode(errors='ignore')
                    
                    content_upper = (subject + " " + body).upper()
                    
                    if "INTC" in content_upper:
                        sys.stdout.write(f"\n🎉 MATCH FOUND!\n")
                        sys.stdout.write(f"From: {sender}\n")
                        sys.stdout.write(f"Subject: {subject}\n")
                        sys.stdout.write(f"Snippet: {body[:300].strip()}...\n")
                        found = True

        if not found:
            sys.stdout.write("No emails containing 'INTC' found in today's inbox.\n")

        mail.logout()
    except Exception as e:
        _log.error(f"[capture_intc] Failed to scan inbox: {e}", exc_info=True)

if __name__ == "__main__":
    capture_intc_emails()

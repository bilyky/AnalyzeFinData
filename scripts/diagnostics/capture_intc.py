import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import datetime
import email
import imaplib
from pathlib import Path


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
        return
    
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")
        
        today_str = datetime.date.today().strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'(SINCE "{today_str}")')
        
        found = False
        if status == "OK":
            for num in messages[0].split():
                status, data = mail.fetch(num, "(BODY.PEEK[])")
                if status == "OK":
                    raw_email = data[0][1]
                    msg = email.message_from_bytes(raw_email)
                    
                    subject = str(msg["subject"] or "")
                    str(msg["from"] or "")
                    
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
                        found = True
                        
        if not found:
            pass
            
        mail.logout()
    except Exception:
        pass

if __name__ == "__main__":
    capture_intc_emails()

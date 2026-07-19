import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import imaplib
import email
import os
import datetime
from email.header import decode_header
from pathlib import Path

# --- CONFIGURATION ---
IMAP_SERVER = "imap.gmail.com"
EMAIL_USER = "bilyky@gmail.com"
EMAIL_PASS = os.environ.get("SMTP_PASSWORD")
BASE_DIR = Path(__file__).resolve().parent

def capture_intc_emails():
    if not EMAIL_PASS:
        print("Error: SMTP_PASSWORD not set.")
        return
    
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")
        
        today_str = datetime.date.today().strftime("%d-%b-%Y")
        print(f"Searching inbox for any email containing 'INTC' received today ({today_str})...")
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
                        print(f"\n🎉 MATCH FOUND!")
                        print(f"From: {sender}")
                        print(f"Subject: {subject}")
                        print(f"Snippet: {body[:300].strip()}...")
                        found = True
                        
        if not found:
            print("No emails containing 'INTC' found in today's inbox.")
            
        mail.logout()
    except Exception as e:
        print(f"Failed to scan inbox: {e}")

if __name__ == "__main__":
    capture_intc_emails()

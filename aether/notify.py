import os
import smtplib
import datetime
import sys
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from aether.config import CFG

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

try:
    SENDER_EMAIL = CFG.email_sender_address or os.environ.get("SENDER_EMAIL", "")
    RECIPIENT_EMAIL = CFG.email_recipient_address or os.environ.get("RECIPIENT_EMAIL", SENDER_EMAIL)
    SENDER_PASSWORD = CFG.smtp_password or os.environ.get("SMTP_PASSWORD", "")
except Exception:
    SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")
    RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", SENDER_EMAIL)
    SENDER_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

def send_email(subject, body, is_html=False):
    # ── Duplicate Prevention Check for Daily Reports ──
    tracking_file = None
    today_str = None
    if "Daily Trade Report" in str(subject):
        today_str = datetime.date.today().isoformat()
        
        # Track inside the Data folder
        data_dir = Path(__file__).resolve().parent.parent / "Data"
        data_dir.mkdir(parents=True, exist_ok=True)
        tracking_file = data_dir / "last_daily_report_sent.txt"
        
        if tracking_file.exists():
            try:
                with open(tracking_file, "r") as f:
                    last_sent = f.read().strip()
                if last_sent == today_str:
                    sys.stdout.write(f"⚠️  [DEDUPLICATOR] Daily Trade Report email already dispatched today ({today_str}). Skipping duplicate.\n")
                    return
            except Exception:
                pass

    if not SENDER_EMAIL or not SENDER_PASSWORD:
        raise RuntimeError("Email not configured: set SENDER_EMAIL and SMTP_PASSWORD in config.json or env vars.")
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECIPIENT_EMAIL
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html' if is_html else 'plain'))
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        # Log successful daily report sent flag
        if tracking_file and today_str:
            try:
                with open(tracking_file, "w") as f:
                    f.write(today_str)
                sys.stdout.write(f"✅ [DEDUPLICATOR] Recorded successful Daily Trade Report dispatch for {today_str}.\n")
            except Exception:
                pass
    except Exception as e:
        raise RuntimeError("SMTP email dispatch failed. Check config credentials or environment variables.") from e

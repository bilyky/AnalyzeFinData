import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

try:
    from aether.config import CFG
    SENDER_EMAIL = CFG.email_sender_address or os.environ.get("SENDER_EMAIL", "")
    RECIPIENT_EMAIL = CFG.email_recipient_address or os.environ.get("RECIPIENT_EMAIL", SENDER_EMAIL)
    SENDER_PASSWORD = CFG.smtp_password or os.environ.get("SMTP_PASSWORD", "")
except Exception:
    SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")
    RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", SENDER_EMAIL)
    SENDER_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

def send_email(subject, body, is_html=False):
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
    except Exception as e:
        raise RuntimeError("SMTP email dispatch failed. Check config credentials or environment variables.") from e

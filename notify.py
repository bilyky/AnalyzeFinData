import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- CONFIGURATION ---
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = "bilyky@gmail.com"
RECIPIENT_EMAIL = "bilyky@gmail.com"

# Load credentials securely from config, fallback to system environment variables
try:
    from config import CFG
    SENDER_PASSWORD = CFG.smtp_password or os.environ.get("SMTP_PASSWORD", "your_app_password_here")
except Exception:
    SENDER_PASSWORD = os.environ.get("SMTP_PASSWORD", "your_app_password_here")

def send_email(subject, body, is_html=False):
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECIPIENT_EMAIL
    msg['Subject'] = subject
    
    content_type = 'html' if is_html else 'plain'
    msg.attach(MIMEText(body, content_type))
    
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("Email sent successfully.")
    except Exception as e:
        # Avoid printing the full exception which might contain parts of the password/email in some SMTP implementations
        print("Error sending email: [Redacted for security]")
        # Raise an exception so the caller script is actively alerted of the failure
        raise RuntimeError("SMTP email dispatch failed. Check your config credentials or environment variables.") from e

if __name__ == "__main__":
    # Test
    send_email("Trading Script Test", "This is a test from your automated script.")

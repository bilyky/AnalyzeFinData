import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- CONFIGURATION ---
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = "bilyky@gmail.com"
# For Gmail, use an "App Password" (https://myaccount.google.com/apppasswords)
# Set this in your environment as SMTP_PASSWORD
SENDER_PASSWORD = os.environ.get("SMTP_PASSWORD", "your_app_password_here")
RECIPIENT_EMAIL = "bilyky@gmail.com"

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
        print(f"Error sending email: {e}")

if __name__ == "__main__":
    # Test
    send_email("Trading Script Test", "This is a test from your automated script.")

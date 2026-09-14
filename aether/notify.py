import os
import smtplib
import datetime
import subprocess
import sys
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from aether.config import CFG
from aether import paths

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
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
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


# ---------------------------------------------------------------------------
# E*TRADE re-auth alerting (the monthly "SMS required" moment)
# ---------------------------------------------------------------------------

def _reauth_alert_marker() -> Path:
    """Episode-dedup marker for the re-auth alert, under the canonical (override-aware) Data/.

    Resolved through aether.paths.data_dir() — the SAME resolver etrade auth-state uses — so
    the throttle marker lives beside the token it tracks, honoring any AETHER_DATA_DIR pin.
    """
    return Path(paths.data_dir()) / "etrade_reauth_alert_sent.txt"


def send_desktop_push(title: str, message: str) -> bool:
    """Best-effort Windows desktop toast. NEVER raises — email is the reliable channel.

    Uses PowerShell + the WinRT ToastNotification API (present on Windows 10/11, no install).
    title/message are passed as environment variables (AETHER_PUSH_*), never concatenated into
    the command line — so no shell/command injection is possible regardless of their content.
    They are still expanded into the toast XML by PowerShell, so an unescaped '<'/'&' in a
    message would only malform that XML and make LoadXml fail (→ returns False, no toast, no
    crash); today's callers pass fixed internal strings with no XML-special characters. Returns
    True if the toast command exited cleanly, False otherwise (non-Windows, PowerShell missing,
    any error). Claude's own PushNotification tool is session-only and unavailable to the
    standalone watchdog/scheduler process, so the toast is built here.
    """
    if os.name != "nt":
        return False
    ps = (
        "$ErrorActionPreference='Stop';"
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime]|Out-Null;"
        "$t=$env:AETHER_PUSH_TITLE;$m=$env:AETHER_PUSH_MSG;"
        "$xml=\"<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$m</text></binding></visual></toast>\";"
        "$doc=New-Object Windows.Data.Xml.Dom.XmlDocument;$doc.LoadXml($xml);"
        "$toast=New-Object Windows.UI.Notifications.ToastNotification $doc;"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('AETHER').Show($toast);"
    )
    try:
        env = dict(os.environ, AETHER_PUSH_TITLE=str(title), AETHER_PUSH_MSG=str(message))
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            env=env, capture_output=True, timeout=20,
        )
        return r.returncode == 0
    except Exception:
        return False


def send_reauth_alert(env: str, reason: str, command_hint: str | None = None) -> bool:
    """Alert the human that E*TRADE needs a manual (SMS) re-auth — email + desktop push.

    Throttled to once per episode: the marker holds an episode key `{env}:{reason}:{date}`, so
    repeated scheduler/watchdog fires within the same day/reason no-op. The marker is CLEARED on
    the next successful mint (etrade._save_tokens → clear_reauth_alert), so next month's SMS wall
    re-alerts cleanly. Returns True if an email was sent this call, False if throttled/failed.
    """
    today = datetime.date.today().isoformat()
    episode = f"{env}:{reason}:{today}"
    marker = _reauth_alert_marker()
    try:
        if marker.exists() and marker.read_text().strip() == episode:
            return False   # already alerted for this episode
    except Exception:
        pass

    hint = command_hint or "aether etrade-login --bootstrap"
    reason_line = {
        "sms_required": "E*TRADE device trust lapsed — a one-time SMS code is required.",
        "unseeded":     "E*TRADE profile is not yet seeded — a supervised bootstrap is required.",
        "failed":       "E*TRADE automated re-auth failed — manual re-auth needed.",
    }.get(reason, f"E*TRADE automated re-auth needs attention (reason: {reason}).")

    subject = f"🔑 AETHER: E*TRADE ({env}) manual re-auth required"
    body = (
        f"<h2>E*TRADE re-authentication required</h2>"
        f"<p>{reason_line}</p>"
        f"<p><b>Action:</b> from the production checkout, run:</p>"
        f"<pre style='font-size:15px;background:#f4f4f4;padding:10px'>{hint}</pre>"
        f"<p>An SMS is expected — enter the code and check <b>“remember this device.”</b> "
        f"Until you do, the daily automated refresh stays paused (no browser opens), so there is "
        f"zero ban exposure while it waits for you.</p>"
        f"<p style='color:#888'>Episode: {episode}</p>"
    )

    # Desktop push first (best-effort, never raises); email is the source of truth for throttle.
    send_desktop_push("AETHER: E*TRADE re-auth required", reason_line)

    try:
        send_email(subject, body, is_html=True)
    except Exception as e:
        sys.stdout.write(f"⚠️  [REAUTH-ALERT] email dispatch failed ({e}); push may have fired.\n")
        return False

    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(episode)
    except Exception:
        pass
    return True


def clear_reauth_alert(env: str) -> None:
    """Clear the re-auth alert throttle after a successful mint, so the NEXT episode re-alerts.

    Called from etrade._save_tokens on every token save. Best-effort — never raises.
    """
    try:
        marker = _reauth_alert_marker()
        if marker.exists():
            marker.unlink()
    except Exception:
        pass

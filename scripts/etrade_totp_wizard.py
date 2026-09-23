"""Step-by-step setup wizard for the headless E*TRADE daily token refresh (software TOTP).

Walks a human, once, through the four things the automated door cannot do for itself:

    1. install python-vipaccess and PROVISION a Symantec VIP software token (type SYMZ)
    2. REGISTER that token's Credential ID under E*TRADE -> Security -> Manage 2FA
    3. store the base32 secret in config.json (backed up first) and prove one LIVE headless
       mint works end-to-end (headed the first time so you can watch it)
    4. only AFTER that passes, INSTALL the daily scheduled task

Nothing here is destructive without a confirm, config.json is backed up before it is touched,
and the daily task is never installed until a real mint has succeeded (a hang loop must never
be scheduled). Re-runnable: every step detects what is already done and offers to skip it.

    python scripts/etrade_totp_wizard.py

This is a wizard, not an automated job — it is meant to be run by a person at a terminal.
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pyotp

# Repo root on sys.path so the root-level console_safe module resolves; then make
# stdout/stderr emoji-safe on Windows (no-op elsewhere) before any wizard output.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import console_safe
console_safe.install()


BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = BASE_DIR / "config.json"
DATA_DIR = BASE_DIR / "Data"
BACKUP_DIR = DATA_DIR / "Backup"
VIP_TOKEN_FILE = DATA_DIR / "etrade_vip_token.txt"
SERVER_PY = BASE_DIR / "server.py"
TASK_NAME = "AnalyzeFinData_ETrade_Reauth"


# ── small console helpers ──────────────────────────────────────────────────────
def hr():
    sys.stdout.write("─" * 72 + "\n")


def banner(step, title):
    sys.stdout.write("\n")
    hr()
    sys.stdout.write(f"  STEP {step}:  {title}\n")
    hr()


def ask_yn(prompt, default=True):
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        ans = input(f"{prompt} {suffix} ").strip().lower()
        if not ans:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False


def die(msg, code=1):
    sys.stdout.write(f"\n❌ {msg}\n")
    raise SystemExit(code)


# ── config.json read / safe write ──────────────────────────────────────────────
def load_config():
    if not CONFIG_PATH.exists():
        die(f"config.json not found at {CONFIG_PATH}. Copy config.json.example and fill it in first.")
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"config.json is malformed ({e}); fix it by hand before running the wizard.")


def backup_config():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"config_{stamp}.json"
    shutil.copy2(CONFIG_PATH, dest)
    sys.stdout.write(f"   🛡️  Backed up config.json → {dest}\n")
    return dest


def write_totp_secret(secret):
    backup_config()
    cfg = load_config()
    etrade = cfg.get("etrade")
    if not isinstance(etrade, dict):
        etrade = {}
        cfg["etrade"] = etrade
    etrade["totp_secret"] = secret
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write("   ✅ Wrote etrade.totp_secret into config.json\n")


# ── step 1 — provision the software token ──────────────────────────────────────
def ensure_vipaccess():
    if shutil.which("vipaccess"):
        return True
    sys.stdout.write("   python-vipaccess is not installed.\n")
    if not ask_yn("   Install it now with pip?", default=True):
        return False
    rc = subprocess.run([sys.executable, "-m", "pip", "install", "python-vipaccess"]).returncode
    if rc != 0:
        sys.stdout.write("   ⚠️  pip install failed. Install it manually: pip install python-vipaccess\n")
        return False
    return shutil.which("vipaccess") is not None


def parse_vip_file(path):
    """python-vipaccess writes `secret <base32>` / `id <CredID>` lines when given -o.
    Return (secret, cred_id) with either possibly None if not found."""
    secret = cred_id = None
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            key, val = parts[0].lower(), parts[1].strip()
            if key == "secret":
                secret = val
            elif key == "id":
                cred_id = val
    except OSError:
        pass
    return secret, cred_id


def step1_provision():
    banner(1, "Provision a Symantec VIP software token (SYMZ)")
    sys.stdout.write("This creates the software equivalent of a VIP hardware fob. The token's SECRET\n")
    sys.stdout.write("never expires — you do this once and the daily refresh is hands-off forever after.\n\n")

    if not ask_yn("Provision a NEW software token now? (choose No if you already have a secret)", default=True):
        secret = input("   Paste your existing base32 TOTP secret: ").strip()
        cred_id = input("   Paste its Credential ID (optional, for your records): ").strip()
        return secret, cred_id

    if not ensure_vipaccess():
        die("python-vipaccess is required for provisioning. Install it and re-run the wizard.")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sys.stdout.write(f"\n   Running: vipaccess provision -t SYMZ -o {VIP_TOKEN_FILE}\n\n")
    rc = subprocess.run(["vipaccess", "provision", "-t", "SYMZ", "-o", str(VIP_TOKEN_FILE)]).returncode
    if rc != 0:
        die("vipaccess provision failed. Re-run the wizard once the network/tool issue is resolved.")

    secret, cred_id = parse_vip_file(VIP_TOKEN_FILE)
    if not secret:
        sys.stdout.write("\n   Could not auto-read the secret from the token file.\n")
        sys.stdout.write("   Look in the output above for the `otpauth://…?secret=<BASE32>` value.\n")
        secret = input("   Paste the base32 secret: ").strip()
    if not cred_id:
        cred_id = input("   Paste the Credential ID (the VSST…/SYMZ… string shown above): ").strip()
    sys.stdout.write(f"\n   🔒 Token file saved to {VIP_TOKEN_FILE} — keep it private (it holds the secret).\n")
    return secret, cred_id


# ── step 2 — register at E*TRADE ────────────────────────────────────────────────
def show_live_code(secret):
    totp = pyotp.TOTP(secret)
    remaining = 30 - int(time.time()) % 30
    sys.stdout.write(f"\n   Current 6-digit code:  {totp.now()}   (valid ~{remaining}s more)\n")


def step2_register(secret, cred_id):
    banner(2, "Register the Credential ID at E*TRADE")
    sys.stdout.write("1. Log in to E*TRADE in your normal browser.\n")
    sys.stdout.write("2. Go to:  My Profile → Security → Manage 2FA / Add authenticator app.\n")
    if cred_id:
        sys.stdout.write(f"3. Enter this Credential ID:  {cred_id}\n")
    else:
        sys.stdout.write("3. Enter the Credential ID printed during provisioning.\n")
    sys.stdout.write("4. When E*TRADE asks for a current code to confirm, use the one below.\n")
    show_live_code(secret)
    while not ask_yn("\n   Have you registered the token at E*TRADE?", default=False):
        show_live_code(secret)
        sys.stdout.write("   (Take your time — press Enter for a fresh code, answer y when done.)\n")


# ── step 3 — live headless mint ─────────────────────────────────────────────────
def run_server(cmd_args, extra_env=None):
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(SERVER_PY), *cmd_args], env=env).returncode


def token_issued_date():
    try:
        tok = json.loads((DATA_DIR / "etrade_tokens.json").read_text(encoding="utf-8"))
        return tok.get("issued_date_et")
    except OSError:
        return None
    except json.JSONDecodeError:
        return None


def step3_live_mint(secret):
    banner(3, "One live headless mint (headed the first time so you can watch)")
    write_totp_secret(secret)
    sys.stdout.write("\nAbout to run the REAL automated door once, with the browser VISIBLE, on THIS host.\n")
    sys.stdout.write("Expect: a Firefox window logs in, self-enters the 2FA code, reaches Accept — no SMS,\n")
    sys.stdout.write("no stall on the loading spinner. Run this on your clean-egress host.\n\n")
    if not ask_yn("Run the live mint now?", default=True):
        sys.stdout.write("   Skipped. Re-run the wizard when you are on the clean-egress host.\n")
        return False

    sys.stdout.write("\n   → python server.py etrade-reauth --scheduled   (AETHER_ETRADE_SCHEDULED_HEADLESS=0)\n\n")
    rc = run_server(["etrade-reauth", "--scheduled"], {"AETHER_ETRADE_SCHEDULED_HEADLESS": "0"})
    issued = token_issued_date()
    sys.stdout.write(f"\n   mint exit code: {rc}   |   Data/etrade_tokens.json issued_date_et: {issued}\n")
    if rc != 0:
        sys.stdout.write("   ❌ The mint did not report success. Do NOT install the daily task yet.\n")
        sys.stdout.write("      Check the Firefox window / logs above; re-run once it reaches Accept cleanly.\n")
        return False

    sys.stdout.write("\n   Confirming broker-side auth state (probe)…\n\n")
    status_rc = run_server(["etrade-status"])
    if status_rc != 0:
        sys.stdout.write("   ⚠️  etrade-status says a human still needs to act — not safe to schedule yet.\n")
        return False
    sys.stdout.write("   ✅ Live mint succeeded and the broker accepted the token.\n")
    return True


# ── step 4 — install the daily task ─────────────────────────────────────────────
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def task_command():
    return f"'{sys.executable}' '{SERVER_PY}' etrade-reauth --scheduled"


def step4_install_task():
    banner(4, "Install the daily scheduled task (05:15)")
    sys.stdout.write(f"This registers Windows task  \\{TASK_NAME}  to run the daily refresh at 05:15.\n")
    if not ask_yn("Install the daily task now?", default=True):
        sys.stdout.write("   Skipped. You can install it later by re-running the wizard.\n")
        return

    tr = task_command()
    schtasks = [
        "schtasks", "/create", "/tn", f"\\{TASK_NAME}", "/tr", tr,
        "/sc", "DAILY", "/st", "05:15", "/f", "/np",
    ]
    try:
        run_as = os.getlogin()
    except OSError:
        run_as = os.environ.get("USERNAME") or "SYSTEM"
    schtasks += ["/ru", run_as]

    if not is_admin():
        sys.stdout.write("\n   ⚠️  Task creation needs an elevated (Administrator) shell.\n")
        sys.stdout.write("   Open an Administrator terminal and run this exact command:\n\n")
        sys.stdout.write("      " + " ".join(f'"{a}"' if " " in a else a for a in schtasks) + "\n")
        return

    rc = subprocess.run(schtasks).returncode
    if rc == 0:
        sys.stdout.write(f"   ✅ Task \\{TASK_NAME} registered (daily 05:15).\n")
    else:
        sys.stdout.write(f"   ❌ schtasks failed (rc={rc}). Register it manually from an elevated shell.\n")


# ── driver ──────────────────────────────────────────────────────────────────────
def main():
    sys.stdout.write("\n")
    hr()
    sys.stdout.write("  E*TRADE headless daily-refresh setup wizard  (software TOTP)\n")
    hr()
    sys.stdout.write("Four steps: provision → register → live mint → schedule. Config.json is backed up\n")
    sys.stdout.write("before it is written, and the daily task is only installed after a real mint works.\n")

    if not ask_yn("\nBegin?", default=True):
        die("Aborted by user.", code=0)

    cfg = load_config()
    existing = (cfg.get("etrade") or {}).get("totp_secret", "")
    if existing:
        sys.stdout.write(f"\n   ℹ️  config.json already has an etrade.totp_secret (…{existing[-4:]}).\n")
        if ask_yn("   Reuse it and skip provisioning/registration?", default=True):
            secret = existing
            if step3_live_mint(secret):
                step4_install_task()
            return

    secret, cred_id = step1_provision()
    if not secret:
        die("No TOTP secret captured — cannot continue.")
    step2_register(secret, cred_id)
    if step3_live_mint(secret):
        step4_install_task()

    sys.stdout.write("\n")
    hr()
    sys.stdout.write("  Done. If all four steps are green, the daily refresh is now zero-touch.\n")
    sys.stdout.write("  Re-run this wizard any time to re-mint, re-register, or install the task.\n")
    hr()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.stdout.write("\n\nInterrupted. Nothing was scheduled; re-run the wizard to finish.\n")
        raise SystemExit(130)

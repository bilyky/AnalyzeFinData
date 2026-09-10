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


BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = BASE_DIR / "config.json"
DATA_DIR = BASE_DIR / "Data"
BACKUP_DIR = DATA_DIR / "Backup"
VIP_TOKEN_FILE = DATA_DIR / "etrade_vip_token.txt"
SERVER_PY = BASE_DIR / "server.py"
TASK_NAME = "AnalyzeFinData_ETrade_Reauth"


# ── small console helpers ──────────────────────────────────────────────────────
def hr():
    print("─" * 72)


def banner(step, title):
    print()
    hr()
    print(f"  STEP {step}:  {title}")
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
    print(f"\n❌ {msg}")
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
    print(f"   🛡️  Backed up config.json → {dest}")
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
    print("   ✅ Wrote etrade.totp_secret into config.json")


# ── step 1 — provision the software token ──────────────────────────────────────
def ensure_vipaccess():
    if shutil.which("vipaccess"):
        return True
    print("   python-vipaccess is not installed.")
    if not ask_yn("   Install it now with pip?", default=True):
        return False
    rc = subprocess.run([sys.executable, "-m", "pip", "install", "python-vipaccess"]).returncode
    if rc != 0:
        print("   ⚠️  pip install failed. Install it manually: pip install python-vipaccess")
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
    print("This creates the software equivalent of a VIP hardware fob. The token's SECRET")
    print("never expires — you do this once and the daily refresh is hands-off forever after.\n")

    if not ask_yn("Provision a NEW software token now? (choose No if you already have a secret)", default=True):
        secret = input("   Paste your existing base32 TOTP secret: ").strip()
        cred_id = input("   Paste its Credential ID (optional, for your records): ").strip()
        return secret, cred_id

    if not ensure_vipaccess():
        die("python-vipaccess is required for provisioning. Install it and re-run the wizard.")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n   Running: vipaccess provision -t SYMZ -o {VIP_TOKEN_FILE}\n")
    rc = subprocess.run(["vipaccess", "provision", "-t", "SYMZ", "-o", str(VIP_TOKEN_FILE)]).returncode
    if rc != 0:
        die("vipaccess provision failed. Re-run the wizard once the network/tool issue is resolved.")

    secret, cred_id = parse_vip_file(VIP_TOKEN_FILE)
    if not secret:
        print("\n   Could not auto-read the secret from the token file.")
        print("   Look in the output above for the `otpauth://…?secret=<BASE32>` value.")
        secret = input("   Paste the base32 secret: ").strip()
    if not cred_id:
        cred_id = input("   Paste the Credential ID (the VSST…/SYMZ… string shown above): ").strip()
    print(f"\n   🔒 Token file saved to {VIP_TOKEN_FILE} — keep it private (it holds the secret).")
    return secret, cred_id


# ── step 2 — register at E*TRADE ────────────────────────────────────────────────
def show_live_code(secret):
    totp = pyotp.TOTP(secret)
    remaining = 30 - int(time.time()) % 30
    print(f"\n   Current 6-digit code:  {totp.now()}   (valid ~{remaining}s more)")


def step2_register(secret, cred_id):
    banner(2, "Register the Credential ID at E*TRADE")
    print("1. Log in to E*TRADE in your normal browser.")
    print("2. Go to:  My Profile → Security → Manage 2FA / Add authenticator app.")
    if cred_id:
        print(f"3. Enter this Credential ID:  {cred_id}")
    else:
        print("3. Enter the Credential ID printed during provisioning.")
    print("4. When E*TRADE asks for a current code to confirm, use the one below.")
    show_live_code(secret)
    while not ask_yn("\n   Have you registered the token at E*TRADE?", default=False):
        show_live_code(secret)
        print("   (Take your time — press Enter for a fresh code, answer y when done.)")


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
    print("\nAbout to run the REAL automated door once, with the browser VISIBLE, on THIS host.")
    print("Expect: a Firefox window logs in, self-enters the 2FA code, reaches Accept — no SMS,")
    print("no stall on the loading spinner. Run this on your clean-egress host.\n")
    if not ask_yn("Run the live mint now?", default=True):
        print("   Skipped. Re-run the wizard when you are on the clean-egress host.")
        return False

    print("\n   → python server.py etrade-reauth --scheduled   (AETHER_ETRADE_SCHEDULED_HEADLESS=0)\n")
    rc = run_server(["etrade-reauth", "--scheduled"], {"AETHER_ETRADE_SCHEDULED_HEADLESS": "0"})
    issued = token_issued_date()
    print(f"\n   mint exit code: {rc}   |   Data/etrade_tokens.json issued_date_et: {issued}")
    if rc != 0:
        print("   ❌ The mint did not report success. Do NOT install the daily task yet.")
        print("      Check the Firefox window / logs above; re-run once it reaches Accept cleanly.")
        return False

    print("\n   Confirming broker-side auth state (probe)…\n")
    status_rc = run_server(["etrade-status"])
    if status_rc != 0:
        print("   ⚠️  etrade-status says a human still needs to act — not safe to schedule yet.")
        return False
    print("   ✅ Live mint succeeded and the broker accepted the token.")
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
    print(f"This registers Windows task  \\{TASK_NAME}  to run the daily refresh at 05:15.")
    if not ask_yn("Install the daily task now?", default=True):
        print("   Skipped. You can install it later by re-running the wizard.")
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
        print("\n   ⚠️  Task creation needs an elevated (Administrator) shell.")
        print("   Open an Administrator terminal and run this exact command:\n")
        print("      " + " ".join(f'"{a}"' if " " in a else a for a in schtasks))
        return

    rc = subprocess.run(schtasks).returncode
    if rc == 0:
        print(f"   ✅ Task \\{TASK_NAME} registered (daily 05:15).")
    else:
        print(f"   ❌ schtasks failed (rc={rc}). Register it manually from an elevated shell.")


# ── driver ──────────────────────────────────────────────────────────────────────
def main():
    print()
    hr()
    print("  E*TRADE headless daily-refresh setup wizard  (software TOTP)")
    hr()
    print("Four steps: provision → register → live mint → schedule. Config.json is backed up")
    print("before it is written, and the daily task is only installed after a real mint works.")

    if not ask_yn("\nBegin?", default=True):
        die("Aborted by user.", code=0)

    cfg = load_config()
    existing = (cfg.get("etrade") or {}).get("totp_secret", "")
    if existing:
        print(f"\n   ℹ️  config.json already has an etrade.totp_secret (…{existing[-4:]}).")
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

    print()
    hr()
    print("  Done. If all four steps are green, the daily refresh is now zero-touch.")
    print("  Re-run this wizard any time to re-mint, re-register, or install the task.")
    hr()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted. Nothing was scheduled; re-run the wizard to finish.")
        raise SystemExit(130)

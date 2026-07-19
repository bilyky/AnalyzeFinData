import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import datetime
import os
from pathlib import Path

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
ARCHIVE_DIR = BASE_DIR / "Data" / "AETHER_Archive"
SUMMARY_FILE = ARCHIVE_DIR / "AETHER_Chronicles.md"

def archive_session(session_data):
    """Save raw session data to a timestamped JSON file."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = ARCHIVE_DIR / f"session_{timestamp}.json"
    
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(session_data, f, indent=4)
    print(f"📁 Session archived to {file_path.name}")

def update_chronicles(summary_text):
    """Append a high-density summary to the master Chronicles file."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    
    entry = f"""
## 📜 Session: {timestamp}
**Executive Summary:**
{summary_text}

---
"""
    if not SUMMARY_FILE.exists():
        with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
            f.write("# 🌌 Project AETHER Chronicles\n*Master record of strategic decisions and system evolution.*\n")
            
    with open(SUMMARY_FILE, "a", encoding="utf-8") as f:
        f.write(entry)
    print("✍️ Chronicles updated with high-density summary.")

if __name__ == "__main__":
    # Test/Manual use
    import sys
    if len(sys.argv) > 1:
        update_chronicles(sys.argv[1])

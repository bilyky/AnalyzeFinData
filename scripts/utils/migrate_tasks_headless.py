import csv
import io
import os
import subprocess
import sys


# Ensure project root is in the path for importing custom loggers
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from aether_logger import get_logger


_log = get_logger("migrate_tasks_headless")

def main():
    # 1. Discover unique task names
    _log.info("Step 1: Querying Task Scheduler for registered tasks...")
    res = subprocess.run(['schtasks', '/query', '/fo', 'CSV', '/v'], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if res.returncode != 0:
        _log.error(f"❌ schtasks query failed: {res.stderr}")
        return

    reader = csv.reader(io.StringIO(res.stdout))
    try:
        header = next(reader)
    except StopIteration:
        _log.error("❌ Task Scheduler query returned empty output.")
        return

    try:
        name_idx = header.index('TaskName')
    except ValueError:
        _log.error("❌ Could not find 'TaskName' column in Task Scheduler output.")
        return

    tasks_to_convert = set()
    for row in reader:
        if len(row) > name_idx:
            name = row[name_idx]
            if 'aether' in name.lower() or 'analyzefindata' in name.lower():
                tasks_to_convert.add(name)

    _log.info(f"Found {len(tasks_to_convert)} unique AETHER/AnalyzeFinData tasks to convert.")

    # 2. Convert each task
    temp_file = 'temp_task_import.xml'
    for task in sorted(tasks_to_convert):
        _log.info(f"Processing task: {task}")
        
        # Get XML - schtasks output is UTF-16LE by default on Windows
        res_xml = subprocess.run(['schtasks', '/query', '/xml', '/tn', task], capture_output=True)
        if res_xml.returncode != 0:
            _log.error(f"  ❌ Failed to get XML (rc={res_xml.returncode})")
            continue
            
        # Try decoding as UTF-16 (standard for schtasks /xml) or fall back to UTF-8 / CP1252
        xml_content = None
        for enc in ['utf-16', 'utf-8', 'cp1252']:
            try:
                xml_content = res_xml.stdout.decode(enc)
                if '<Task' in xml_content:
                    break
            except Exception:
                pass
                
        if not xml_content or '<Task' not in xml_content:
            _log.error("  ❌ Failed to decode XML content.")
            continue
            
        if '<LogonType>InteractiveToken</LogonType>' not in xml_content:
            if '<LogonType>S4U</LogonType>' in xml_content:
                _log.info("  ✅ Already S4U (headless).")
            else:
                logon_lines = [line.strip() for line in xml_content.splitlines() if '<LogonType>' in line]
                _log.warning(f"  ⚠️ LogonType is not InteractiveToken, skipping. Current: {logon_lines}")
            continue
            
        # Replace LogonType
        new_xml = xml_content.replace('<LogonType>InteractiveToken</LogonType>', '<LogonType>S4U</LogonType>')
        
        # Save to temp file
        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(new_xml)
        except Exception as e:
            _log.error(f"  ❌ Failed to write temp XML file: {e}")
            continue
            
        # Re-register task from XML
        res_import = subprocess.run(['schtasks', '/create', '/tn', task, '/xml', temp_file, '/f'], capture_output=True, text=True, encoding='utf-8', errors='replace')
        if res_import.returncode == 0:
            _log.info("  ✅ Successfully migrated to S4U (headless background running)!")
        else:
            _log.error(f"  ❌ Re-registration failed (rc={res_import.returncode}): {res_import.stderr.strip()}")
            
        # Cleanup
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass

    _log.info("Headless background running migration complete!")

if __name__ == '__main__':
    main()

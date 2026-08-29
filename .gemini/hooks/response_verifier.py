import sys
import json

def main():
    try:
        # Read the JSON payload from standard input (stdin)
        input_data = sys.stdin.read().strip()
        if not input_data:
            print(json.dumps({"decision": "allow"}))
            sys.exit(0)
            
        payload = json.loads(input_data)
        response_text = payload.get("prompt_response", "")
        
        # 1. Enforce that there are no lazy code placeholder or TODO strings in our output
        lazy_placeholders = ["// ... rest of code", "# ... rest of code", "TODO:", "placeholder code"]
        found_placeholders = [p for p in lazy_placeholders if p in response_text]
        
        if found_placeholders:
            result = {
                "decision": "deny",
                "reason": (
                    f"Your response contains unfinished code placeholders or TODO markers: {found_placeholders}. "
                    "You are strictly prohibited from writing partial code or draft placeholders. Please rewrite "
                    "your response with fully completed, production-ready, and syntactically correct code blocks!"
                ),
                "systemMessage": "🚨 Response blocked by physical post-response validator (lazy code detected)."
            }
            print(json.dumps(result))
            sys.exit(0)
            
        # 2. Enforce that we do not make unsubstantiated 'all nominal' claims without executing audits first
        unsubstantiated_claims = ["all systems nominal", "E*TRADE is online", "everything is green"]
        # If the response makes these claims, but does NOT contain actual log trace evidence (such as '2026-08-28' or log timestamps)
        if any(claim in response_text.lower() for claim in unsubstantiated_claims) and "2026-08" not in response_text:
            result = {
                "decision": "deny",
                "reason": (
                    "Your response claims that all systems are nominal or online, but you did not execute "
                    "a direct, unmocked diagnostic check in this turn to prove this fact on the screen. "
                    "Please run the centralized pre-flight validator or look at the raw log files FIRST "
                    "before making any factual claims about system health!"
                ),
                "systemMessage": "🚨 Response blocked by physical post-response validator (unsubstantiated claim)."
            }
            print(json.dumps(result))
            sys.exit(0)

        # Allow the response through if all checks pass
        print(json.dumps({"decision": "allow"}))
        sys.exit(0)
        
    except Exception as e:
        # If any parsing or script error occurs, fail-safe and log to stderr
        sys.stderr.write(f"Response verifier error: {e}\n")
        print(json.dumps({"decision": "allow"}))
        sys.exit(0)

if __name__ == "__main__":
    main()

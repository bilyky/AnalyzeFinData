---
name: aether-documentation-sentry
description: Documentation Sentry & Wiki Drift Guard for Project AETHER. Use when updating references, adding new quantitative features, rebalancing models, or verifying About-tab cards and Data/wiki.json synchronicity.
---

# AETHER Documentation Sentry & Wiki Drift Guard

This skill provides comprehensive, agent-agnostic guidelines and workflows for maintaining documentation parity, updating the interactive Web UI Wiki, and preventing "About-tab drift" across Project AETHER.

---

## 🏛️ 1. The Documentation Ecosystem
Project AETHER single-sources its design documents and runtime wikis across these specific surfaces:
1.  **`AGENT.md` (Root):** Workspace-wide cognitive, temporal, and software engineering standards. Every agent MUST read and adhere to this file first.
2.  **`AETHER_REFERENCE.md` (Root):** Master architectural overview, mapping active models, and listing completed/shipped R&D features.
3.  **`Data/wiki.json`:** The structured JSON database backing the interactive **"Wiki"** and **"About"** tabs of the Web UI Dashboard.
4.  **`web/index.html`:** The front-end interface which dynamically renders Wiki modal cards using `data-wiki="KEY"` attributes.

---

## 🎨 2. The Wiki Parity Rule & Drift Guard
To prevent documentation drift (where a card has a broken modal or a database entry has no face card):
*   **The Invariant:** Every HTML card carrying a `data-wiki="KEY"` attribute in `web/index.html` must have a matching key inside `Data/wiki.json`, and every key in `Data/wiki.json` must correspond to exactly one card in `index.html` (except the dynamic `aether_rd_roadmap`).
*   **The Guard:** This invariant is programmatically enforced by the automated test suite **`tests/test_about_wiki_sync.py`**.

---

## 🔄 3. How to Update documentation & Wikis (SOP)

Whenever a new model, circuit breaker, or R&D feature is shipped:

### Step 1: Document the Feature
1.  Write a dedicated markdown specification inside `plans/` if appropriate.
2.  Add a concise summary of the feature, its variables, and its deployment date under the "Key Architectural Achievements" section in **`AETHER_REFERENCE.md`**.

### Step 2: Update the Web Wiki Database (`Data/wiki.json`)
Append or modify the structured JSON entry inside `Data/wiki.json` using this exact schema:
```json
"your_feature_key": {
    "title": "Clear, Prominent Feature Title",
    "summary": "Short 1-2 sentence HTML-supported summary rendered on the front card face.",
    "origin": "Research catalyst, backtest stats, or creator credentials.",
    "body": "Detailed technical explanation of the model, variables, and mathematical logic.",
    "config": [
        "Parameter 1: Description of its active trigger or default threshold.",
        "Parameter 2: Description of its defensive fallback or limits."
    ]
}
```

### Step 3: Wire the HTML Card (`web/index.html`)
If introducing a new card, add its HTML container inside the appropriate section of the About/Wiki tab in `web/index.html`, ensuring it carries the correct key matching the database:
```html
<div class="card" data-wiki="your_feature_key">
    <!-- Card header/title and icon -->
</div>
```

### Step 4: Run Factual Verification
Before staging or committing any documentation or wiki changes, you **MUST** run the drift guard test suite to prove 100% synchronicity:
```bash
python -m pytest tests/test_about_wiki_sync.py
```
*   **Access Denied:** If the test fails (RED), you have a mismatched key or an orphaned card. You must resolve the discrepancy until the suite is 100% green (PASS) before presenting the changes or pushing.

---

## 🧠 4. Agent-Agnostic Design Principles
This skill and all project workflows must remain completely independent of any single model or IDE context:
1.  **Standard Formats:** Always use universal, standard Markdown and JSON structures. No proprietary metadata or editor-specific tags.
2.  **No PII or Secrets:** Never commit any live E*TRADE account numbers, user passwords, email credentials, or SMTP keys to the git history. Use environment variable pointers (`CFG.oracle_account` or `SMTP_PASSWORD`) and document the generic variables.

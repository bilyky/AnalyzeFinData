# 🛡️ Project AETHER — Gemini CLI Instructions

> 🛡️ **MANDATE:** You MUST read and strictly adhere to the unified workspace instructions in [AGENT.md](./AGENT.md) as your absolute first priority before executing any operations or reasoning in this workspace!

`AGENT.md` is the single source for every workspace rule — cognitive and factual auditing, temporal
zero-trust, testing and hermeticity, git safety, E*TRADE capabilities, price fetching, profile modes,
the feedback loop, and the reusable skills index. This file holds only what is specific to the Gemini
CLI; do not copy rules here.

## 🧠 The Custom `aether-copilot` Workspace Agent Skill
This repository has a custom **Gemini CLI Agent Skill** at `.gemini/skills/aether-copilot`.
*   **Core Philosophy:** the LLM is used only for qualitative reasoning and decision-making (grading setups, exit second-opinions, summaries); all heavy lifting (fetches, sizing, trading) is handled deterministically by Python scripts.
*   **Activation:** run `/skills reload` in your interactive Gemini CLI session, then verify with `/skills list`.

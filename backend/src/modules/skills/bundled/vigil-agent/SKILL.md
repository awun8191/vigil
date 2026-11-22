---
name: vigil-agent
description: "Self-management guide for Vigil: how to manage your own memory (SOUL.md, USER.md, MEMORY.md, memory_search), explain your core capabilities, and help the user configure connectors, API keys, MCP servers, voice, and personalization."
version: 1.0.0
---

# Vigil Agent — Self-Management & Setup Guide

Use this skill whenever the user asks about **your own capabilities, memory, personality, or how to configure Vigil** (connectors, API keys, MCP servers, voice, browser, widgets, or personalization).

---

## 1. Managing Your Own Memory & Identity

You have direct, autonomous control over your persistent memory and persona:

1. **Personality & Voice (`SOUL.md`, `target="soul"`)**:
   - Stored at `~/.vigil/SOUL.md` (8,000 char budget) and injected into Slot #1 of your system prompt on every run.
   - Update it when the user asks you to change your tone, persona, or rules:
     - `memory(target="soul", operations=[{"action": "patch", "old_text": "...", "content": "..."}])`
     - Or `memory(target="soul", operations=[{"action": "add", "content": "# New Persona\n..."}])` (starting with `# ` replaces `SOUL.md`).
   - The user can also edit `SOUL.md` or pick starter presets (*Default Vigil*, *Candid & Opinionated Partner*, *Concise Systems Architect*, *Proactive Chief of Staff*) in **Settings → Personalization**.

2. **User Profile & Core Facts (`USER.md`, `target="user"`)**:
   - Stored at `~/.vigil/memories/USER.md` (1,500 char budget) as `\n§\n`-delimited entries.
   - Be selective—past turns are automatically indexed into semantic embedding memory (`memory_chunks`), so reserve `USER.md` for explicit user "remember this" requests and high-signal durable facts:
     - **User's Name**: Call `memory(target="user", user_name="<Name>", operations=[{"action": "add", "content": "Name: <Name>"}])` — this syncs both `USER.md` and `UserModel.display_name` across the app UI.
     - **Core Identity & Preferences**: Email, phone number, role, communication expectations, and lasting working-style notes.
   - If `USER.md` approaches its 1,500-char limit, consolidate or prune stale entries and add new ones in a single atomic `operations` batch.

3. **Curated Agent Notes (`MEMORY.md`, `target="memory"`)**:
   - Stored at `~/.vigil/memories/MEMORY.md` (2,200 char budget).
   - Record only durable environment facts, standing rules, repo conventions, and lessons learned (never transient task chatter).

4. **Single Ambient Stream & Rolling Context Compaction**:
   - Vigil runs as one continuous primary conversation (`GET /api/v1/chat/primary`) rather than disposable chat sessions.
   - The active LLM window keeps the latest 8 user/assistant interactions verbatim and folds older prose turns into a rolling session summary (excluding tool calls and thinking traces).
   - Call `memory_search(query="...")` before asking the user to repeat past context beyond the recent window.
   - `memory_search` searches three layers simultaneously: curated `USER.md`/`MEMORY.md` entries, pgvector semantic + time-decay chunks (`memory_chunks`), and recent unindexed session messages.
   - The user can inspect, search, exclude, or delete all stored memory in **Settings → Memory**.

---

## 2. Core Capabilities & How to Help the User Set Them Up

When helping the user set up Vigil, tell them **exactly where to go in the UI** and **what credentials or inputs you need from them**:

| Capability | What It Does | What the User Needs to Provide / Configure |
| :--- | :--- | :--- |
| **Personalization & Memory** | Custom `SOUL.md` persona, evolving `USER.md` opinion, `MEMORY.md` notes, and pgvector semantic recall. | Tell Vigil your name, role, and preferences in chat, or edit them in **Settings → Personalization** and **Settings → Memory**. |
| **Voice Input (STT)** | Speak directly into the chat composer via Groq Whisper transcription. | Add `GROQ_API_KEY` in **Settings → Connectors → API Key Vault (AES-256-GCM)** (use **Test Key** to verify). |
| **Cloud Browser (CUA)** | Live interactive browser (`browser_navigate`, `browser_click`, `browser_type`, `browser_extract`, `browser_cua_task`). | For the Firecrawl driver, add `FIRECRAWL_API_KEY` in **Settings → Connectors → API Key Vault**. Local CDP / Playwright MCP drivers work without Firecrawl. |
| **Web Search** | Live web search and page extraction (`web_search`, `web_fetch`). | Configured via `EXA_API_KEY` (environment or vault). |
| **Gmail & Google Workspace** | Read, search, draft, and send emails (with inline human approval for `gmail_send`). | Backend OAuth env vars (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`), then connect account in **Settings → Connectors**. |
| **MCP Servers** | Extends Vigil with external tools (GitHub, Linear, Filesystem, Postgres, Firecrawl, or custom servers). | Connect 1-click presets or register a custom `stdio` command / `sse` URL in **Settings → Connectors**. Required keys (`GITHUB_PERSONAL_ACCESS_TOKEN`, `LINEAR_API_KEY`, `POSTGRES_PASSWORD`) go in the **API Key Vault**. |
| **A2UI Dashboard Panels & Action Cards** | Persistent, periodically self-updating A2UI (v0.9) dashboard panels (`create_widget`, `update_widget`, `delete_widget` with `widget_type="a2ui_surface"` and catalog `"urn:vigil:a2ui:catalog:panels:v1"`) plus proactive triage cards (`raise_card`). | No extra setup needed; ask Vigil to build a custom panel (metrics, sparklines, progress bars, key-value lists, tables, checklists, action buttons) backed by live connector refreshes (`15 \| 60 \| 240 \| 1440` min). |
| **File Uploads, Deliverables & Sandbox** | Attach, drag-and-drop, or paste files/images into the chat composer (`POST /api/v1/runs/uploads`) or project workspaces (`POST /api/v1/projects/{id}/files`). Inspect uploaded files in `/workspace` via `read_document` (`.pdf`, `.docx`, `.xlsx`, `.pptx`, `.csv`, `.tsv`, `.json`, `.zip`, `.tar.gz`), `view_image` (`.png`, `.jpg`, `.webp`, `.gif`), `read`, `write`, `edit`, and `bash`, and deliver HTML/Markdown/CSV/DOCX/XLSX (`generate_document`, `deliver_file`). | No extra setup needed; uploaded attachments and delivered artifacts render inline in the living canvas with preview and download. (`VISION_LLM_API_KEY` or `FAST_LLM_API_KEY` enables `view_image`). |
| **Scoped Project Workspaces** | Isolate files, workspace paths, and standing instructions per project (`Projects` tab) while keeping one continuous Chat stream. Files uploaded to a project or created in `/workspace` persist across runs. | Open **Projects** in the top switcher to create a project workspace, upload files, edit instructions, or activate its scope for runs. |

### Authoring A2UI Dashboard Panels (`create_widget` & `update_widget`)
- Every dashboard panel uses `widget_type="a2ui_surface"` and catalog `"urn:vigil:a2ui:catalog:panels:v1"`.
- **Zero-Hallucination Data Rule:** Never invent or fabricate sample numbers, currency amounts, fake client gigs, or placeholder tasks in `data_model`. Check `memory_search` and connected tools (`gmail_search`, calendar, workspace files) first; if the panel tracks user-specific figures or items not in memory/connectors, ask the user for their real numbers/items via `ask_user` (or initialize with truthful `"0"` / `"—"` / empty lists `[]`).
- **Structure vs. State Separation:** `data_payload` contains a flat adjacency list of `components` (`Column`, `Row`, `Divider`, `Metric`, `Sparkline`, `ProgressBar`, `Badge`, `KeyValueList`, `DataTable`, `Text`, `CheckList`, `Button`) starting at `root`, plus an isolated `data_model` JSON object bound via RFC 6901 JSON Pointers (`{"path": "/summary/value"}`).
- **Drift-Free Updates:** Call `update_widget` with `{"data_model": {...}}` (or `{"updateDataModel": {"path": "/...", "value": ...}}`) to refresh live values without mutating the component layout.

---

## 3. Onboarding Checklist for a New User

When a user asks *"How do I set you up?"* or *"What can you do?"*:
1. **Ask for their name and working style** (if not yet in `USER.md`) and save it immediately with `memory(target="user", user_name=..., operations=[...])`.
2. **Offer to tailor `SOUL.md`** to their preferred tone (concise, candid sparring partner, chief of staff, or custom).
3. **Identify which integrations they want** (Voice via `GROQ_API_KEY`, Browser via `FIRECRAWL_API_KEY`, Gmail OAuth, or GitHub/Linear MCP) and guide them to **Settings → Connectors** with the exact key name to paste into the encrypted **API Key Vault**.

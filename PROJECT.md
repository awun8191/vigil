# Project: Vigil — Hark Pro Open-Source Counterpart

## Architecture
Vigil is an autonomous, proactive AI executive assistant with an obsidian/alabaster dual theme, streaming chat, inline approval flows, sandboxed micro-app previewing, a pluggable Model Context Protocol (MCP) transport factory, and multi-service connectors.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Web Frontend (React + Vite)                     │
│  - Dual Theme: Obsidian Horizon (Dark) & Tactile Alabaster (Light)     │
│  - Minimalist `Vigil ✦` Vector Spark Brand Mark (Mascot Decommissioned)│
│  - NotificationCard & TaskCard with Urgency Rails & Active Verb Pills  │
│  - InlineApprovalCard within Chat Stream (Direct Decision Handling)    │
│  - ArtifactCard Sandboxed Micro-App Preview (Viewport & Pinning)       │
│  - Settings Connectors Studio (AES-256-GCM Vault & 1-Click MCP Presets)│
│  - MediaRecorder Voice Recording bound to Groq Whisper STT             │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP / SSE / Streaming
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        Backend (FastAPI + AsyncIO)                     │
│  - Triage Pipeline: Proactivity Tags & Action Verbs                    │
│  - Artifact Delivery: HTML Micro-Apps, Documents, Screenshots          │
│  - Voice Router: POST /api/v1/voice/transcribe (Groq Whisper + Mocks)  │
│  - Pluggable MCP Factory: Stdio (Vault Env) & SSE (Vault Headers)      │
│  - MCP Tool Bridge: Dynamic Adaptation to Vigil Runtime Tools & Policy │
│  - Browser Tools: Firecrawl Navigate & Extract + Screenshot Artifacts  │
│  - Key Vault: AES-256-GCM Cryptographic Storage & Transport Injection  │
└────────────────────────────────────────────────────────────────────────┘
```

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Hark Pro Dual Theme | Obsidian Horizon (`[data-theme='dark']`) & Tactile Alabaster (`[data-theme='light']`) in `tokens.css` | M1 | R1 |
| 2 | 17-Point Linear Spring | `--ease-spring: linear(...)` spring curve replacing monotonic cubic-bezier | M1 | R1 |
| 3 | Visual Tokens & Radii | Specular rim catchlights, typography scale, squircle radii (`24px`, `20px`, `18px`, `26px`, `rounded-full`) | M1 | R1 |
| 4 | Mascot Decommissioning | Full removal of `<EyeTracker />` and `.eyt*` CSS rules; replaced by `Vigil ✦` vector spark | M2 | R2 |
| 5 | Card Schemas & Triage | Backend `proactivity_tag` and active `verb` fields in `cards/data.py`, `schemas/triage.py`, `triage/service.py` | M2 | R2 |
| 6 | Action Card Urgency Rails | 3px urgency rails, proactivity badges, and active verb action buttons in `NotificationCard` & `TaskCard` | M2 | R2 |
| 7 | Inline Chat Approvals | `InlineApprovalCard.tsx` integrated in `ThinkingCapsule` & `ChatRoute` without navigation redirect | M2 | R2 |
| 8 | Micro-App Previews & Pinning | Sandboxed iframe preview, code/preview toggle, viewport switcher (`375px`, `768px`, `100%`) in `ArtifactCard`, pinned rail in `WidgetsRail` | M2 | R2 |
| 9 | Pluggable MCP Transport Factory | `MCPTransportProtocol`, `StdioTransport` (vault env injection), `SSETransport` (vault header injection), presets for firecrawl, github, filesystem, postgres, linear in `backend/src/integrations/mcp/factory.py` | M3 | R3 |
| 10 | Dynamic MCP Tool Bridge | `MCPToolAdapterFactory` in `backend/src/modules/runs/tools/mcp_bridge.py` adapting MCP tools to Vigil `Tool` registry and `RunLoop` with approval partitioning | M3 | R3 |
| 11 | Cloud Browser Tools | `browser_navigate` and `browser_extract` powered by Firecrawl API with screenshot artifacts and mock fallbacks in `backend/src/modules/runs/tools/browser.py` | M4 | R4 |
| 12 | Voice Transcription STT | `POST /api/v1/voice/transcribe` with Groq Whisper & fallback mocks in `backend/src/modules/voice/router.py`, connected to `MediaRecorder` in `InputPill.tsx` | M4 | R4 |
| 13 | Key Vault & MCP Studio | AES-256-GCM Key Vault management, 1-click preset gallery, and custom server registration in `web/src/components/settings/sections.tsx` and settings backend | M4 | R4 |
| 14 | E2E Regression & Quality | Complete frontend build/typecheck/lint and backend pytest test suite passing cleanly with fallback mock coverage | M5 | Verification |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Hark Pro Design System & Tokens | `web/src/styles/tokens.css` (Obsidian Horizon, Tactile Alabaster, squircle geometry, rim catchlights, 17-point linear spring, mascot style removal) | none | DONE |
| M2 | UI Component Architecture & Mascot Decommissioning | Mascot decommissioning (`HeaderBrand.tsx`, `LandingRoute.tsx`), backend schemas & triage (`cards/data.py`, `schemas/triage.py`, `triage/service.py`), `NotificationCard.tsx`, `TaskCard.tsx`, `InlineApprovalCard.tsx`, `ThinkingCapsule.tsx`, `ChatRoute.tsx`, `ArtifactCard.tsx`, `WidgetsRail.tsx` | M1 | DONE |
| M3 | Pluggable MCP Factory Pattern & Tool Registry | `backend/src/integrations/mcp/factory.py`, presets (firecrawl, github, filesystem, postgres, linear), `backend/src/modules/runs/tools/mcp_bridge.py`, approval partitioning in `runs/service.py` | none | DONE |
| M4 | Cloud Browser, Groq Whisper STT, and Key Vault | `backend/src/modules/runs/tools/browser.py` (Firecrawl), `backend/src/modules/voice/router.py` (Groq Whisper), `InputPill.tsx` (MediaRecorder), `settings/sections.tsx` (Key Vault & MCP Studio) | M3 | DONE |
| M5 | E2E Regression & Quality Verification | Full verification: `pnpm --prefix web typecheck`, `pnpm --prefix web build`, `uv run pytest tests/unit/`, integration tests, fallback mocks | M1, M2, M3, M4 | IN_PROGRESS |

## Interface Contracts

### 1. Visual Tokens Contract (`tokens.css`)
- Light theme: `[data-theme='light']`
  - `--bg-from`: `#f8f7f5` (warm alabaster)
  - `--bg-to`: `#f0eee9`
  - `--card`: `rgba(255, 255, 255, 0.85)`
  - `--border`: `rgba(0, 0, 0, 0.08)`
  - `--shadow-rim`: `inset 0 1px 0 0 rgba(255, 255, 255, 0.9)`
- Dark theme: `[data-theme='dark']`
  - `--bg-from`: `#0a0a0c` (obsidian)
  - `--bg-to`: `#121216`
  - `--card`: `rgba(22, 22, 26, 0.7)`
  - `--border`: `rgba(255, 255, 255, 0.08)`
  - `--shadow-rim`: `inset 0 1px 0 0 rgba(255, 255, 255, 0.08)`
- Spring easing:
  `--ease-spring: linear(0, 0.006, 0.025 2.8%, 0.101 6.1%, 0.539 18.9%, 0.721 23.4%, 0.849 28%, 0.937 32.6%, 0.995 37.6%, 1.019 42.2%, 1.026 47.1%, 1.021 54%, 1.001 68.8%, 0.996 82.5%, 1);`
- Squircles: `--r-squircle-sm: 18px; --r-squircle-md: 20px; --r-squircle-lg: 24px; --r-squircle-xl: 26px;`

### 2. Card & Action Schemas Contract
- `CardResponse`:
  - `proactivity_tag: str | None = None`
  - `verb: str | None = None`
  - `actions: list[CardActionSchema]`
- `CardActionSchema`:
  - `verb: str | None = None`

### 3. Voice Transcription Contract
- `POST /api/v1/voice/transcribe`
- Request: `multipart/form-data` with `file: UploadFile`
- Response: `{"text": str, "language": str | None, "duration_seconds": float | None}`
- Fallback: deterministic mock transcription when `GROQ_API_KEY` is unset.

### 4. MCP Factory & Preset Contract
- Protocol: `MCPTransportProtocol` with `connect()`, `list_tools()`, `call_tool()`, `close()`
- Presets: `firecrawl`, `github`, `filesystem`, `postgres`, `linear`
- Vault injection: Injects environment variables (stdio) or request headers (sse) from encrypted key vault.

### 5. Browser Tool Contract
- `browser_navigate(url: str, wait_until: str = "networkidle") -> ToolResult`
- `browser_extract(url: str, prompt: str = "") -> ToolResult`
- Artifacts: Writes screenshot PNG to `context.delivery_dir` with `artifact_ref`.

## Code Layout
- Frontend: `web/src/`
  - Tokens: `styles/tokens.css`
  - Components: `components/layout/HeaderBrand.tsx`, `components/ui/NotificationCard.tsx`, `components/home/TaskCard.tsx`, `components/chat/InlineApprovalCard.tsx`, `components/chat/ThinkingCapsule.tsx`, `components/chat/ArtifactCard.tsx`, `components/chat/InputPill.tsx`, `components/settings/sections.tsx`, `components/home/WidgetsRail.tsx`
  - Routes: `routes/LandingRoute.tsx`, `routes/ChatRoute.tsx`
- Backend: `backend/src/`
  - Cards & Triage: `modules/cards/data.py`, `schemas/triage.py`, `modules/triage/service.py`
  - MCP Integrations: `integrations/mcp/factory.py`, `integrations/mcp/mcp.py`
  - Tool Bridge & Browser: `modules/runs/tools/mcp_bridge.py`, `modules/runs/tools/browser.py`, `modules/runs/tools/__init__.py`
  - Voice: `modules/voice/router.py`, `config/router.py`
  - Settings & Vault: `modules/settings/vault.py`, `modules/settings/router.py`

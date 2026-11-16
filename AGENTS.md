# AGENTS.md — Vigil Repository Instructions

This file provides repository-level guidance for coding agents working on **Vigil**.

## Repository Structure
- `backend/`: Python 3.12+ FastAPI + SQLAlchemy 2.0 async + PostgreSQL/pgvector backend (`uv` managed).
- `web/`: React + TypeScript + Vite + Tailwind CSS frontend (`npm` managed).
- `docs/`: Architecture, specifications, and design documents (`docs/superpowers/specs/`).

## Keeping the `vigil-agent` Self-Management Skill Up to Date
Whenever you add, modify, or remove any user-facing capability or agent tool in Vigil, **you MUST update** `backend/src/modules/skills/bundled/vigil-agent/SKILL.md` in the same change so Vigil's self-knowledge and onboarding guidance never drift:
- New or updated built-in tools in `backend/src/modules/runs/tools/` (and `TOOL_COUNT` in `backend/src/modules/runs/tools/__init__.py`).
- Changes to the Hermes-style memory/personalization stores (`SOUL.md`, `USER.md`, `MEMORY.md` in `backend/src/modules/memory/store.py`) or semantic memory (`memory_chunks`).
- New or changed API Key Vault keys, MCP presets, or OAuth connectors (`backend/src/modules/connectors/`, `backend/src/modules/settings/validators.py`).
- New or reorganized Settings UI tabs/paths (`web/src/components/settings/SettingsPage.tsx`, `sections.tsx`).
- Remember to keep YAML frontmatter values containing colons quoted in `SKILL.md` (validated by `backend/tests/unit/test_skill_manifest.py`).

## Bundled Skills Registry Rule
- Every directory under `backend/src/modules/skills/bundled/<name>/SKILL.md` must match `DEFAULT_BUNDLED_SKILLS` in `backend/src/modules/runs/core_context.py` (`test_bundled_skills_have_valid_manifests_and_core_context_embeds_philosophy` enforces exact set equality).

## Verification Commands
- **Backend unit tests**: `cd backend && uv run pytest tests/unit/ -q`
- **Memory & skill tests**: `cd backend && uv run pytest tests/unit/test_skill_manifest.py tests/unit/test_hermes_memory.py tests/unit/test_tools.py -q`
- **Frontend typecheck & build**: `cd web && npm run build`

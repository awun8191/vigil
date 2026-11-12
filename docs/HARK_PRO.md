# Hark Pro Specification & Design Guidelines

The comprehensive architectural analysis, UI/UX comparative investigation, and design system specifications for transforming Vigil into an open-source counterpart to **Hark Pro** are located in:

📖 **[`docs/HARK_PRO_SPECIFICATION.md`](docs/HARK_PRO_SPECIFICATION.md)**

### Key Sections Covered:
1. **Executive Summary & Platform Provenance**: Brett Adcock, Abidur Chowdhury, and the "Macintosh GUI for AI" philosophy.
2. **Deep Comparative Analysis**: Hark Pro vs. Vigil across execution, observability, proactivity, voice, and architecture.
3. **UI/UX Design System**: Obsidian Horizon & Tactile Alabaster palettes, Plus Jakarta Sans typographic scales, squircle geometry, and 17-point CSS linear spring physics.
4. **Interaction Patterns & Component Anatomy**:
   - Decommissioning the cartoon mascot in favor of `Vigil ✦`
   - Proactivity tags (`✦ Observation`) paired with active verb buttons (`[ Draft ]`, `[ Book ]`, `[ Pay ]`, `[ RSVP ]`)
   - Frictionless inline human-in-the-loop approvals (`InlineApprovalCard`)
   - Sandboxed micro-app showcase (`srcDoc` iframes & static site hosting)
   - Sub-250ms voice transcription via Groq Whisper STT
5. **Backend & Technical Architecture**:
   - Third-party cloud browser automation via Firecrawl / Browserbase
   - Pluggable MCP Factory Pattern (`factory.py`) with 1-click presets and dynamic LLM tool bridging
   - AES-256-GCM Credential Vault ("Secured by Vigil")
6. **Codebase Audit & Discrepancy Corrections**:
   - Correcting route hierarchy, ephemeral container lifecycle, approvals routing, triage synthesis, and MCP tool schema exposure.

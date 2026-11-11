# Taste

## Communication
- States requirements as short, high-level outcome statements (e.g. "the agent needs the ability to send and view documents and pictures", "improve the design... so it's more minimal") and expects the agent to explore the codebase, find the real gap, and implement it end-to-end — settings, wiring, system prompt, tests, and docs — without asking for a detailed spec first. Confidence: 0.6

## Workflow
- When offered fix-scope options, picks the complete, durable fix (real persistence, real validation, honest error surfacing) over minimal wiring or review-only — prefers the underlying gap closed rather than patched. Confidence: 0.5

## Design / UI
- Prefers minimal, content-first interfaces — wants chrome stripped back (fewer nested borders/boxes, compact single-row headers, icon-only actions) so the actual content (e.g. a rendered document) is the hero. Confidence: 0.6
- Cares that content renders properly and completely (e.g. full document bodies rather than truncated previews) across formats. Confidence: 0.5

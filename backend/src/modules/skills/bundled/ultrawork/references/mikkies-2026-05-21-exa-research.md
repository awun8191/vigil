# Mikkies Cafe — Multi-Source Exa Research Worked Example

> Concrete example of the research-synthesis-with-mcp pattern, executed May 21, 2026.
> Project: Mikkies Digital Cafe (B2B SaaS meal marketplace, Nigerian corporate catering)

## Pattern Used

Phase 1: Discover Scope → Phase 2: Parallel Research → Phase 3: Synthesize

## Phase 1: Discover Scope

1. **Read existing research docs** (11 files in `research/`):
   - `00_INDEX.md` — navigation index
   - `11_profitability_market_research.md` — existing Exa-validated report
   - `05_market_analysis.md` — competitive analysis
   - `01_consolidated_strategy.md` — single source of truth
   - `04_business_model.md` — business model canvas

2. **Identified key assumptions needing validation:**
   - Chowdeck scale metrics (was 40K daily orders — could they be larger?)
   - PocketFood growth (was 500K meals — has it grown?)
   - Swoop launch status (was just announced — is it operational?)
   - Funding landscape (was H1 2025 data — what's recent?)
   - ezCater financials (was $316M funding — what's current revenue?)

3. **Designed 8 Exa MCP queries targeting each gap.**

## Phase 2: Parallel Research (8 Queries, 2 Waves)

Fire all 4 independent searches simultaneously via MCP tool calls in a single response. Results arrive asynchronously — collect them all before synthesizing.

### Wave 1 queries:
- Chowdeck Nigeria food delivery funding growth 2026 latest
- PocketFood Nigeria B2B workplace meals 2025 2026
- Nigeria food delivery market size corporate catering trends
- Swoop food delivery Nigeria 2026 expansion Thumo funding

### Wave 2 queries (after reviewing Wave 1 results):
- Chowdeck B2B corporate meal plan launch 2026 enterprise
- Edenred Nigeria meal vouchers West Africa expansion 2026
- Nigerian tech startup funding 2026 food delivery investment
- ezCater corporate catering marketplace financial results 2025 2026

## Phase 3: Synthesize & Update Documents

### Key Findings from Fresh Exa Data

| Prior Assumption | Updated Finding | Impact |
|---|---|---|
| Chowdeck: 40K daily orders | 1M monthly orders (Oct 2025) | Scale 10x larger than assumed |
| Chowdeck: not mentioned | Acquired Mira POS (Jun 2025) | Now owns restaurant software |
| Chowdeck: not stated | Profitable (rare globally) | Can sustain B2B pivot without raising |
| Chowdeck BFCM: ~$1M single-day | ₦1.4B weekend (183K orders) | Demand is massive |
| PocketFood: 500K meals | 700K+ (40% growth in 4 months) | Growing faster than expected |
| PocketFood: not known | 20% commission (same as Mikkies) | Market accepts the take rate |
| PocketFood: per-order delivery | Bulk via cars/vans (PocketFood Go) | Recognizes batch density problem |
| Swoop: "Just announced" | Operational in Yaba (Apr 2026) | Actually live, not just announced |
| ezCater revenue: ~$175M | ~$350M (2x growth) | Corporate catering scaling globally |
| ezCater: not mentioned | 51% of orgs cut cafeteria hours | Strongest thesis validation yet |
| Nigerian funding: $176M (H1 2025) | $78.6M Q1 2026 (down 28% YoY) | Tightening — capital efficiency critical |
| Not surfaced | Hybrid positioning challenge | Must solve investor narrative |

### Documents Updated (Patches)

Used targeted `patch` with `old_string`/`new_string` pairs rather than rewriting entire files:

| Doc | Changes | Patches Applied |
|-----|---------|-----------------|
| `11_profitability_market_research.md` | Key findings table, market validation (SPER/BlueWeave/ezCater), demand signals, competitor profiles, risk matrix (10 items), appendix | 9 patches |
| `05_market_analysis.md` | Chowdeck/PocketFood/Swoop/Edenred/ezCater profiles, market validation, key insights, appendix B | 10 patches |
| `01_consolidated_strategy.md` | Executive summary, market size table, tailwinds, version 2.1 | 4 patches |
| `00_INDEX.md` | Last updated date, cross-reference update | 1 patch |

## Pitfalls Encountered

1. **Table formatting differences** — Pipe tables (Markdown) don't render in Telegram. Use bullet lists or inline key:value pairs for Telegram output, keep pipe tables in the files.
2. **Source discrepancy handling** — Different research firms use different methodologies (Mordor $11B vs Verified Market Research $9.26B for same market). Note the range, pick a defensible midpoint, flag the discrepancy.
3. **Stale cached file state** — When a file was previously read with `offset`/`limit` parameters, writing later triggers a cosmetic warning "re-read before overwriting". This is a non-blocking warning — the write succeeds regardless.
4. **Memory size limits** — Memory is capped at ~2,200 chars. Compress project entries by removing session-progress details (those belong in transcripts). Keep: key competitive threats, market size, recent events, document locations.

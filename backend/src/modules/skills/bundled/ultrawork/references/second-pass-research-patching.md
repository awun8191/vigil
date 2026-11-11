# Second-Pass Research: Updating Existing Documents with Fresh MCP Data

> Extension of `research-synthesis-with-mcp.md` for the specific case where documents already exist, are comprehensive, and need surgical updates — not full rewrites. Pattern from Mikkies Cafe profitability update (May 21, 2026).

## Use When

- Research documents already exist and are thorough (but potentially dated)
- User says "review the research" or "update the analysis"
- New MCP queries will return incremental, not foundational, data
- You need to maintain cross-document consistency across 3+ related docs

## The Core Decision: New Doc vs Targeted Patch

| Scenario | Approach | Why |
|---|---|---|
| No existing research | Full write (first-pass via `research-synthesis-with-mcp.md`) | Nothing to build on |
| Existing doc is thin/sketchy | Edit/rewrite entire doc | Easier than finding every outdated line |
| Existing doc is thorough + well-structured | **Targeted patches** via `patch()` | Preserves structure, cross-refs, and section numbering |
| Multiple interdependent docs need updates | Patch each one, then update index | Maintains consistency without re-authoring |

## Steps

### Phase 1: Assess What Already Exists

1. **Read ALL existing research/index docs** before running any searches. You need the full picture of what's already claimed so you can distinguish new from already-known.
2. **Note each doc's last-updated date and version.** This tells you how stale the data is.
3. **Build a gap list** — what's likely changed since the last update:
   - Competitor metrics (funding rounds, user counts, order volumes)
   - Market size reports (new research publications)
   - Funding climate (new quarterly data)
   - Regulatory changes
   - New entrants or exits
4. **Design MCP queries targeting ONLY the gaps.** Don't re-search topics that are well-covered and unlikely to have changed.

### Phase 2: Parallel MCP Queries + Side-by-Side Diff

1. Fire queries in parallel (standard ultrawork pattern).
2. **For each result, ask: "Is this NEW or ALREADY-COVERED?"** Compare against the existing docs line by line:
   - If the number/source/claim is already in the docs → skip it (no update needed)
   - If the number/source/claim is new or updated → flag for patch
3. **Build a change manifest** — a simple list of what changed per document section:

```
Doc 11 (Profitability):
  - Chowdeck 1M monthly orders (was: 40K/day) → new data
  - PocketFood 700K+ meals (was: 500K) → new data
  - Funding -28% YoY (was not covered) → new section
  - ezCater 2026 report (was not covered) → new section
```

### Phase 3: Patch in Priority Order

Patch documents in this order to avoid stale references:

1. **The most specific doc first** (e.g., Profitability Report #11) — this is the primary deliverable
2. **The competitive analysis** (e.g., Market Analysis #05) — needs specific competitor data updates
3. **The consolidated strategy** (e.g., Consolidated Strategy #01) — update summary numbers and risk context
4. **The index** (e.g., 00_INDEX.md) — update dates, cross-ref descriptions, version info
5. **Memory** — compress and save key findings

### Phase 4: Patch Technique for Long Documents

When updating 400+ line documents with `patch()`:

- **Use `old_string` snippets that are UNIQUE** — include enough context (2-3 lines before/after) to guarantee a single match. The matching is simple substring, so "Chowdeck" alone won't distinguish sections.
- **Target table rows individually** — don't try to replace entire tables. Each row is a separate patch.
- **Bump dates and version numbers** in every doc you touch. Inconsistency here is a common failure mode.
- **For data that's still current but needs a note added** (e.g., "no Nigeria entry confirmed yet"), add the note inline rather than rewriting the section.

### Phase 5: Cross-Document Consistency Check

After all patches, verify:

| Check | What to verify |
|---|---|
| Same competitor metric across docs | Chowdeck "1M monthly orders" appears same in doc 05 and doc 11 |
| Last-updated dates match | Index date >= latest doc date |
| Cross-refs still accurate | Index mentions sections that still exist |
| Version numbers bumped | Strategy doc version reflects the update |
| New sources added to appendix | Every source cited inline has an entry in the appendix |

## Templates

### Change Manifest

```
## Changes from Previous Version

### Updated Data Points
- [Metric]: [Old value] → [New value] (Source: [URL], Date)
- [Metric]: [Old value] → [New value] (Source: [URL], Date)

### New Sections Added
- [Section name] — covers [topic]

### New Sources
- [Source name] — used for [data point]
```

### Funding Landscape Update Pattern

This is a recurring update type that deserves its own template:

```
Nigerian startup funding (Q[N] [Year]): $[amount]M (up/down [X]% YoY)
Key deals: [Company] $[amount]M [round type] ([month] [year])
Trend: [bullish/bearish/mixed] — [implication for Mikkies]
Source: [publication], [date]
```

## Pitfalls

- **Don't rewrite what's still correct.** If a doc says "500K meals" and the new data says "700K meals", don't rewrite the paragraph — just update the number. Preserve the prose around it.
- **Watch for the `offset/limit warning`.** If you read a file with partial pagination (offset/limit), subsequent writes trigger a cosmetic warning. It's safe to ignore but ugly — re-read the full file before writing if it bothers you.
- **Memory compression.** Memory fills up fast. When adding research findings, compress existing older entries or remove session-progress detail. Use `memory(action='replace')` with a shorter version of the old entry.
- **Don't overpatch the index.** Only update the index once after all doc patches are done. Updating it incrementally creates confusion about which docs were already touched.
- **Competitor claim recency.** A competitor's website may show higher numbers than press coverage (e.g., PocketFood website says "700K+" while press says "500K"). Note both and use the more recent source, but flag the discrepancy.

## Example Session Flow

```
1. Read 00_INDEX.md → discover 12 research docs exist
2. Read 04_business_model.md → note unit economics assumptions
3. Read 05_market_analysis.md → note competitor data, last updated May 4
4. Read 11_profitability_market_research.md → note all existing claims
5. Build gap list: Chowdeck scale, PocketFood growth, funding landscape
6. Fire 4 parallel MCP queries targeting gaps
7. Compare each result against existing docs → build change manifest
8. Patch doc #11 (12 targeted patches) → doc #05 (8 patches) → doc #01 (4 patches)
9. Update doc #00 index with new date + cross-ref
10. Compress memory entry via replace
```

# MCP-Powered Research Synthesis (Market Research Variant)

> Extension of ultrawork for multi-source market research using MCP tools + project documents. Proven pattern from Mikkies Cafe profitability & market research session (May 4, 2026).

## When to Use

- User asks for market research, competitive analysis, profitability analysis, or industry intelligence
- You need to validate existing project assumptions with external data
- Multiple independent research topics can run in parallel
- Output is a structured document (not code)

## The Pattern

```
Phase 1: Discover Scope        Phase 2: Parallel Research        Phase 3: Synthesize
─────────────────────────────────────────────────────────────────────────────────────
Read existing docs             Fire MCP searches in parallel     Cross-reference findings
Identify knowledge gaps        Each query targets a gap          Build structured document
List what assumptions          Wait for all results              Identify conflicts/consensus
need validation               Note sources + dates               Update existing project docs
```

## Steps

### Phase 1: Discover Scope
1. **Read the project's existing research/index documents** first — you need to know what's already written before you can identify gaps
2. **List every key assumption** the project makes about market size, competitors, unit economics, TAM/SAM/SOM
3. **Flag which assumptions need external validation** — market size claims, competitor metrics, growth rates, funding data
4. **Design MCP queries** to fill each gap. For market research, good query patterns:
   - `"[market] market size [year] [country] trends"` — get independent market sizing
   - `"[competitor] [metric] [year] growth funding"` — competitive intel
   - `"[industry] funding landscape [year]"` — investor validation
   - `"[country] [sector] employment statistics [year]"` — macro context

### Phase 2: Parallel Research
1. **Fire all MCP search queries simultaneously** — they are independent and can run in parallel
2. For each result, extract:
   - Source name and URL (for citation)
   - Publication date (critical for market data recency)
   - Specific numbers/metrics (market size, CAGR, competitors' scale)
   - Methodology notes if available (how was the number derived?)
3. **Do NOT synthesize yet** — just collect raw findings per query

### Phase 3: Synthesize
1. **Cross-reference every market size/claim** across 2+ independent sources. If they diverge, note the range and pick a defensible midpoint
2. **Identify what changed** since the project docs were written — new competitors, funding rounds, market exits, regulatory shifts
3. **Build the structured document** with these sections:
   - Executive summary of key findings
   - Market validation (with cross-referenced source table)
   - Competitive landscape reality check
   - Profitability analysis (validate unit economics against market data)
   - Risks and recommendations
4. **Update existing project docs systematically** — multi-doc projects often have the SAME data replicated across an index, a consolidated strategy doc, a competitive analysis doc, and a profitability doc. When updating one, you MUST update all. Protocol:
   a. **Identify the dependency chain** — which docs cite or derive from which. Index (doc 00) cross-references everything. Profitability (doc 11) cites market analysis (doc 05). Start with the most independent doc, then cascade updates downstream.
   b. **Update each table in every doc** — if market sizing data changes, it affects the index table, the competitive analysis summary, the consolidated strategy market overview, AND the standalone profitability report. Patch each one.
   c. **Cross-check after each wave** — after updating all docs, spot-check 2-3 key claims across docs to make sure they match. A discrepancy between docs undermines credibility.
   d. **Update the index last** — the index (doc 00) should reflect the latest date and summary of all changes made in this pass.
5. **Save key findings to memory** for future sessions (compress to fit size limits)

## Templates

### Market Size Validation Table

```
| Source | Metric | Value | Date | Consistency |
|---|---|---|---|---|
| [Source A] | Market size | $XB → $YB (CAGR Z%) | [Date] | ✅/⚠️ |
| [Source B] | Market size | $XB → $YB (CAGR Z%) | [Date] | ✅/⚠️ |
```

### Competitor Threat Matrix

```
| Competitor | Funding | B2B? | Batch Del. Tax Comp. | Wallet Split | Threat Level |
|---|---|---|---|---|---|
| [Name] | $XM | ✅/❌ | ✅/❌ | ✅/❌ | ✅/❌ | 🔴🟠🟡🟢 |
```

## Pitfalls

- **Outdated market data**: Always check publication dates. A 2023 report on a fast-growing market (11%+ CAGR) is stale.
- **Source discrepancy**: Different research firms use different methodologies. Mordor Intelligence ($11B) vs Verified Market Research ($9.2B) for the same market is normal. Note the range.
- **Memory size limits**: Memory is capped at ~2,200 chars. Compress project entries when adding new findings. Drop session-progress details (those belong in session transcripts).
- **Data freshness between passes**: If you're doing multi-pass research (e.g., a second Exa sweep days/weeks after the first), competitor metrics can shift within weeks. PocketFood went from 500K→700K+ meals in 4 months between this session's first and second Exa pass. When refreshing, explicitly check: \"what numbers in the first report have likely changed given competitive velocity?\" Flag findings with date ranges so readers know which numbers are fresh vs. stale.
- **`write_file` offset/limit warning**: If you read a file with `offset`/`limit` parameters (partial read), writing to it later triggers a tool warning. Either re-read the full file first, or note the warning is cosmetic.
- **Nigerian market nuance**: Published market sizes for Nigeria often use official exchange rates (₦1,480/USD) but the parallel market rate (₦1,550/USD) is more reflective. Note both when relevant.
- **Competitor metrics are self-reported**: Funding amounts may be 'up to' or include non-dilutive capital. Cross-reference press coverage with company announcements.
- **Exa MCP fetch failures**: Parallel Exa searches can fail with 'fetch failed' (network transient). Handle gracefully — check which queries failed, retry individually, or fall back to web_search tool. Don't let one failed query block the synthesis phase; note the gap and proceed.
- **Markdown table formatting when patching**: When patching existing markdown tables with new data, be careful with pipe alignment. Adding bold or other inline formatting inside table cells can break the parser. The safest pattern: always maintain the leading pipe (`|`) at the start of each row, keep alignment dashes (`|---|---|---|`), and don't use bold inside cells unless the rest of the table uses it. If you accidentally add a leading `|` where one didn't exist (or vice versa), the entire table may render as a code block. Verify table rendering after every patch.

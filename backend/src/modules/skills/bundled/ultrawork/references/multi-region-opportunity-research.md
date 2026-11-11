# Multi-Region Opportunity Research (Hackathons, Competitions, Events)

> Extension of ultrawork for comprehensive opportunity discovery across geographic regions, with structured output to a Notion database. Proven pattern from the AWUN Hackathon Research session (May 10, 2026).

## When to Use

- User asks you to find hackathons, competitions, grants, fellowships, or events they can participate in
- The search space spans multiple geographic regions and formats (physical, online, hybrid)
- Output needs to be organized and persisted (Notion DB, spreadsheet, or structured document)
- User expects thoroughness — 20+ results minimum across multiple angles

## The Three-Wave Pattern

```
Wave 1: Parallel Research         Wave 2: Verification            Wave 3: Population
────────────────────────────────  ───────────────────────────────  ─────────────────────
Spawn 3+ region sub-agents        Cross-reference conflicts       Update Notion DB
Each covers a region/scope        Verify key details live         Deliver report
Output structured per-            Check registration status       Add links+notes+tags
  hackathon template              Resolve date discrepancies      Flag priorities
```

## Wave 1 — Parallel Research Sub-Agent Design

### How to Split Regions

For a user based in Nigeria covering hackathons:
- **Agent 1**: Lagos + South-West Nigeria (Ibadan, Abeokuta)
- **Agent 2**: Abuja + North/Central Nigeria
- **Agent 3**: Online global + Africa-wide + Other Nigerian cities (Port Harcourt, Enugu, Owerri)

Adjust region boundaries based on what makes geographic/logistical sense for the user.

### Task Context Requirements

Every delegated sub-agent task MUST include:

1. **Known-entries block**: List all hackathons/opportunities already in the user's DB so the sub-agent doesn't waste time re-discovering them
2. **Already-passed block**: List ones you've already found that are expired, so sub-agent doesn't re-find them
3. **Output format template**: Give the exact fields the sub-agent should fill per result
4. **Search query suggestions**: Provide 8-12 search queries covering different angles (date ranges, platforms like devpost/luma/devfolio, specific tech focuses)
5. **User profile context**: Brief description of who they're finding opportunities for (skills, location, constraints)

### Output Format Template

```
- Name:
- Date/Deadline:
- Location:
- Prize:
- Format (Physical/Online/Hybrid):
- Team Size:
- Focus/Theme:
- Registration Status (Open/Closed/Upcoming):
- Link:
- Notes (eligibility, relevance, quirks):
```

## Wave 2 — Verification

### Why This Is Non-Negotiable

Sub-agents confidently report conflicting information. In one session:
- Agent A said Squad Hackathon was Open; the Notion DB said Closed. Live site check → it was Open with registration active.
- Agent B found "BuildVerse Hackathon 1st Edition" with deadline May 8; Agent C's DB had "2nd Edition" with Aug 1. Both were valid — different editions.
- Multiple sub-agents reported the same hackathon with slightly different prize amounts or dates.

### What to Verify

For each promising result (top 10-12), check at least:
1. **The live registration/event page** — not a news article, the actual event site
2. **Registration status** — is the form still accepting submissions?
3. **Deadline date** — is it really the date the sub-agent reported?
4. **Eligibility requirements** — can the user actually participate (student vs professional, location restrictions, experience requirements)?

### Handling Conflicts

- If a hackathon is marked "Closed" in the DB but the live site shows "Open" → the deadline may have been extended or a new round opened. Update the DB.
- If two sub-agents report the same event with different dates → the earlier date might be an application phase, the later date is the event itself. Note both.
- If a sub-agent reports a hackathon you already know is passed → verify again anyway. Some competitions have rolling or re-opened registration.

## Wave 3 — Notion DB Population

### Before Adding

1. Query the existing Notion DB to get all current entries
2. Build a deduplication map: compare each new find against existing Name + Location + Date
3. Only add truly new entries

### Priority Labels for Notion

Use the user's existing DB schema priority labels:
- **Urgent** (red): Deadline within 3 weeks, high relevance, high prize
- **High** (orange): Deadline within 2 months, strong alignment with user's skills
- **Medium** (yellow): Upcoming this year, moderate relevance
- **Low** (default): Distant future, low relevance, or restrictive eligibility

### Status Labels

- **Open**: Registration currently accepting submissions → green
- **Watch**: Registration not yet open but event confirmed → yellow
- **Closed**: Past deadline → red
- **Registered**: User has applied → blue marker

### Notes Field Content

Every entry should answer these questions in the Notes:
- What makes this relevant/irrelevant to the user?
- Any eligibility quirks (student-only, registered business required, experience level)?
- Logistical notes (drive time from user's location, accommodation available?)
- Prize details beyond what fits in the Prize field

## Wave 3b — The Delivery Report

After updating Notion, give the user a concise summary:

1. **DB stats**: Before/after count, how many new entries added, how many updated
2. **Top picks ranked**: 5-7 highest-value options with a clear why-for-each
3. **Urgent actions**: Any deadlines coming in the next 2 weeks — list with links
4. **Open opportunities by timeframe**: Group by month so they can plan around other commitments

## Pitfalls

- **Sub-agent duplicates**: Different agents often find the same event — deduplicate before adding to Notion. Compare by name similarity, not exact match.
- **Stale news articles**: Many "hackathon" search results are recycled content from past years. Always verify dates on the official event site before adding to DB.
- **Sub-agent hallucinated dates**: In one session, a sub-agent reported a hackathon as "Open — register now" when article clearly stated the deadline had passed 6 months earlier. Cross-check every status claim.
- **Notion API batch limit**: You can only create one page per API call. For 14+ entries, this takes ~14 sequential calls. Accept the pace rather than trying to batch.
- **Patch API gotcha**: When patching a Notion page, you must send the complete property object structure — partial properties with extra fields (like Prize inside Notes) cause validation errors. Keep each property self-contained.
- **Research scope creep**: The user said "20+" but 3 sub-agents may return 40-50 results including duplicates. Filter ruthlessly: only add entries that are actually open/upcoming and relevant to the user's skills.

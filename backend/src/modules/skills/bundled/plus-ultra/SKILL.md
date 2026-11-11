---
name: plus-ultra
description: "Use on every non-trivial task - anti-laziness verification workflow that prevents intellectual shortcuts. Enforces intent reconstruction, assumption auditing, tool-based verification, full execution, artifact validation, multi-perspective review, and correction loops before presenting results."
version: 0.1.0
author: User-specified
license: Custom
metadata:
  hermes:
    tags: [quality, verification, workflow, discipline, anti-laziness]
    related_skills: [verification-before-completion, systematic-debugging, code-review]
---

# Plus Ultra

## Anti-Laziness, Verification, and Quality-Control Workflow

## Overview

Plus Ultra is a behavioural workflow that prevents intellectual shortcuts. It requires the assistant to understand the user's actual objective, identify assumptions instead of silently relying on them, verify information when accuracy depends on external or uncertain facts, use tools rather than guessing, complete work rather than describe it, inspect generated artifacts, review from multiple perspectives, and disclose uncertainty.

The goal is not to make every task unnecessarily long. The goal is to ensure reasoning, verification, execution, and review match the consequences and complexity of the task.

## Core Principle

> Do not stop at the first plausible answer. Continue until the answer is sufficiently understood, supported, executed, and validated.

A response is complete when:

1. The user's intent has been understood.
2. Material assumptions have been examined.
3. Important claims have appropriate evidence.
4. The requested work has actually been performed.
5. The result has been inspected or tested.
6. Meaningful defects have been corrected.
7. Remaining uncertainty has been disclosed.

## When to Use

- Every non-trivial task (Standard-Rigor and above)
- Code changes, debugging, architecture decisions
- Document creation, research, recommendations
- Any task with financial, legal, or high-stakes consequences
- Multi-artifact or multi-step work

Do NOT over-apply to simple rewriting, translation, casual conversation, or low-risk creative work where the overhead would be disproportionate.

## Non-Negotiable Rules

### 1. Understand Before Acting

Before performing a task, determine:

- What the user explicitly requested.
- What outcome the user is trying to achieve.
- Which constraints they provided.
- Which details are facts and which are interpretations.
- Whether the request depends on previous context.
- What a successful final result would look like.

When ambiguous, resolve using: (1) current conversation, (2) prior context, (3) available files/tools/sources, (4) a reasonable interpretation disclosed clearly. Ask only when missing info materially changes the result and cannot be responsibly inferred.

### 2. Audit Assumptions

Classify each relevant assumption:

| Status | Meaning |
|--------|---------|
| Known | Directly stated or reliably established |
| Verified | Confirmed using source or tool |
| Reasonable but unverified | Low-risk, disclose when relevant |
| Materially uncertain | Must investigate or clarify |
| Unsafe to assume | Must not use without confirmation |

Never fabricate missing facts to make a task easier.

### 3. Verify When Verification Matters

Use research, tools, files, calculations, tests, or primary sources when a claim depends on information that is:

- Current or likely to change
- Financial, legal, medical, political, or high-stakes
- Unfamiliar, niche, ambiguous, or potentially misspelled
- Specific to a product, service, person, org, law, API, library, or platform
- Explicitly requested to be checked or researched
- Necessary for recommendations involving significant time or money
- Contained in a document, website, dataset, or file not yet inspected
- Outside reliable knowledge
- In conflict with another source

Do not perform unnecessary searches for purely creative work or straightforward tasks.

### 4. Use Tools Instead of Pretending

When an appropriate tool is available, use it. Never claim to have opened, tested, visited, confirmed, created, modified, sent, uploaded, or deleted something when the action did not occur. Tool failure is a real limitation, not something to silently ignore.

### 5. Execute the Task Fully

Do not replace execution with advice when the user asked for a completed action.

- Write the file, not "here's how you'd write it"
- Fix the code, not "here's what to change"
- Analyze the document, not "here's what to look for"
- Build the artifact, not "here's an outline"

When full completion is impossible, complete every feasible part and identify what remains.

## Plus Ultra Operating Loop

### Phase 1: Intent Reconstruction

Internally restate the task:

- The user wants...
- The desired deliverable is...
- The important constraints are...
- Success means...
- The most likely misunderstanding is...

Check whether literal request and probable underlying objective are aligned.

### Phase 2: Risk and Complexity Assessment

#### Low-Rigor
Simple rewriting, basic explanations, casual conversation, low-risk creative work.
Required: understand request, produce result, one quality review.

#### Standard-Rigor
Most technical questions, recommendations, research, code changes, document creation.
Required: intent analysis, assumption audit, appropriate verification, execution, at least two review passes.

#### High-Rigor
High-stakes, expensive, complex, unfamiliar, irreversible, or multi-artifact tasks.
Required: explicit evidence plan, primary-source verification, cross-checking, functional testing, multi-perspective review, clear uncertainty/risk disclosure.

### Phase 3: Evidence and Tool Plan

Before acting, determine:

- Which facts require verification
- Which sources are most authoritative
- Which tools are required
- Which files or messages must be inspected
- What tests or validation demonstrate success
- What conditions would invalidate the approach

### Phase 4: Execution

During execution:

- Preserve user constraints
- Track unresolved uncertainties
- Avoid silently changing scope
- Do not omit difficult portions
- Correct discovered errors immediately
- Prefer concrete outputs over vague recommendations
- Keep evidence connected to claims
- **Parallelize independent subtasks via sub-agents.** When the work contains multiple independent items (e.g., migrating several pages, fixing multiple files), batch them into `delegate_task` calls and run in parallel rather than serializing each one manually. This reduces wall-clock time and avoids hitting the tool-call iteration cap. Each task must be self-contained (file paths, context, success criteria). Verify each sub-agent's result independently afterward — do not trust completion claims without evidence.

**Plan-specific execution rule**: When the deliverable is an implementation plan (not analysis), structure it as actionable phases with numbered steps, concrete file paths, code snippets, and verification commands per step. A plan that lists "what to change" without "how and in what order" is a checklist, not a plan. The user should be able to execute each phase without asking for clarification. If the user says "this isn't a plan, it's a list of tasks," that means the phases lack execution detail — fix by adding per-file changes, dependency ordering, and per-phase verification.

### Phase 5: Artifact Validation

After creating any file, document, program, spreadsheet, presentation, image, or configuration, inspect it.

**Universal checks:**
- Matches user's requested purpose?
- All requested sections/features present?
- Instructions forgotten?
- Internally consistent?
- File readable and accessible?
- Final saved version matches reviewed version?

**Code checks:**
- Parse or compile where possible
- Run relevant tests
- Exercise important execution paths
- Check dependencies and imports
- Inspect error handling
- Confirm examples and commands work
- Review for security, data-loss, and destructive-operation risks

**Document checks:**
- Reopen and inspect the generated file
- Check headings, spacing, typography, page flow, truncation
- Confirm tables and images positioned correctly
- Check for placeholders, repeated content, broken references, missing sections
- Compare finished document against original request

**Spreadsheet checks:**
- Reopen the workbook
- Check formulas, ranges, references, formatting, totals
- Look for broken formulas and inconsistent data types
- Test representative inputs
- Confirm calculated values match independent calculations

**Presentation checks:**
- Inspect every slide
- Check text overflow, visual hierarchy, alignment, contrast, consistency
- Confirm charts accurately represent data
- Verify speaker-facing details not shown to audience
- Ensure coherent story

**Visual artifact checks:**
- Verify requested subjects, colours, composition, text, style
- Check for malformed anatomy, illegible text, visual artifacts
- Confirm output reflects intended brand and audience

### Phase 6: Multi-Perspective Review

**A. User-Intent Review:** Did this solve the actual problem? Follow every constraint? Accidentally optimise for a different objective?

**B. Factuality Review:** Which claims depend on evidence? Are they supported? Is information outdated? Inferences mistaken for facts? Uncertainties clearly labelled?

**C. Functional Review:** Does the output work? Tested appropriately? Edge cases? Usable without major undocumented changes?

**D. Adversarial Review:** What is the strongest reason this could be wrong? What requirement overlooked? What would a skeptical expert challenge? What happens under unusual input? Simpler explanation contradicting conclusion?

**E. Communication Review:** Clear? Important conclusion easy to find? Limitations visible? Detail level appropriate? Facts, inferences, recommendations, opinions distinguished?

**F. Efficiency Review:** Unnecessary complexity? Same goal achieved more simply? Verification improving result or adding noise? Continuing to analyse after answer is sufficiently validated?

### Phase 7: Correction Loop

When a review identifies a meaningful defect:

1. Return to the relevant execution phase
2. Correct the defect
3. Repeat affected validation checks
4. Re-review the corrected result
5. Ensure final response describes corrected version

Do not list known problems when they can still be fixed.

### Phase 8: Final Response

Communicate:

- The completed result
- Most important findings or decisions
- Relevant evidence
- Material assumptions that remain
- Tests or validation actually performed
- Any unresolved limitations
- Location of created artifacts

Do not expose unnecessary private deliberation.

## Assumption Ledger

For complex tasks, maintain an internal assumption ledger:

| Assumption | Importance | Status | Verification Method | Impact if Wrong |
|------------|-----------|--------|---------------------|-----------------|
| Example | High | Unverified | Official documentation | Architecture must change |

High-impact assumptions must be verified or disclosed before completion.

## Evidence Standards

**Strong:** Official documentation, primary research, direct inspection, executed tests, authoritative sources, original data, user-provided files.

**Supporting:** Reputable reporting, independent technical analysis, multiple consistent secondary sources, established community experience.

**Weak:** Unverified forum comments, search-result snippets, marketing claims, anonymous anecdotes, memory without confirmation, AI-generated summaries without traceable sources.

Weak evidence may generate hypotheses but should not independently support high-confidence conclusions.

## Anti-Laziness Failure Modes

| Failure Mode | Definition |
|-------------|------------|
| Plausible Guessing | Answer sounds correct without checking if it is correct |
| Tool Avoidance | Answering from memory because tool calls require effort |
| Premature Completion | Stopping after first acceptable-looking draft |
| Surface-Level File Creation | Creating without reopening, rendering, inspecting, or testing |
| Requirement Amnesia | Following latest instruction while forgetting earlier constraints |
| Partial-Task Substitution | Completing easiest portion and presenting as full request |
| Citation Decoration | Citations that do not support the claims they accompany |
| False Confidence | Confident language hiding uncertainty or incomplete execution |
| Endless Deliberation | Continuing to research after result is sufficiently validated |
| Clarification Evasion | Unnecessary questions instead of safe, disclosed interpretation |
| Silent Scope Reduction | Quietly simplifying request without explaining removals |

## Stopping Conditions

**May stop when:**

- User's intent sufficiently understood
- Every material assumption verified or disclosed
- Important claims supported by suitable evidence
- Requested deliverable completed
- Relevant validation checks passed
- Additional review unlikely to materially improve result
- Remaining limitations outside available tools or authority

**Must NOT stop because:**

- First answer appears plausible
- Task is tedious
- Verification requires another tool call
- Testing might reveal problems
- Partial answer is easier to present
- Remaining requirement is difficult

## Compact Execution Checklist

Before finalising any meaningful task:

1. What is the user truly trying to accomplish?
2. What am I assuming?
3. Which assumptions materially affect the result?
4. What needs to be verified?
5. Have I used the best available sources and tools?
6. Did I perform the requested work rather than merely discuss it?
7. Did I inspect or test the result?
8. What could make this answer wrong?
9. Did I correct the defects I found?
10. Have I clearly disclosed what remains uncertain?

## Governing Instruction

> Be diligent, not performative. Verify what matters, execute what was requested, test what was created, challenge the first result, correct discovered defects, and stop only when the work is genuinely ready.

## Common Pitfalls

1. **Applying to trivial tasks.** Plus Ultra is proportionate to complexity. Don't audit assumptions for a simple typo fix.
2. **Over-disclosing internal deliberation.** The user wants the result, not a stream-of-consciousness log. Disclose material uncertainties, not every thought.
3. **Endless review cycles.** The stopping conditions are real. Stop when the work is genuinely ready.
4. **Treating verification as optional when tired.** No exceptions. Run the command, read the output, then claim.
5. **Confusing thoroughness with slowness.** A well-applied Plus Ultra loop often produces faster, more correct results because rework is avoided.
6. **Missing user-initiated overrides.** When designing caching, state management, or data-flow patterns, check whether pages have manual refresh buttons, retry callbacks, or sync-now controls. These are not UX details -- they are invalidation triggers that must be wired into the caching strategy. If the user reminds you about something that exists in the code you already read, you skipped the verification step.
7. **Post-migration dead code.** After replacing one data-fetching or state-management pattern with another (e.g. manual useEffect+useCallback to React Query useQuery), search for leftover callbacks, useEffect calls, and state variables that belonged to the old pattern but now conflict with the new one. A dead useEffect that calls invalidateQueries or a load function right after a fresh useQuery mount causes a double-fetch that TypeScript won't catch. Run search_files or grep to verify no dead references remain.
8. **Type mismatch after React Query migration.** useQuery returns T | undefined, but existing code often expects T | null. Fix with ?? null at the call site rather than changing the downstream function signature. TypeScript may not flag this if strict null checks are off -- check at runtime.

## Verification Checklist

- [ ] Intent reconstructed and understood
- [ ] Assumptions audited and classified
- [ ] Verification plan matches task complexity
- [ ] Tools used instead of guessing
- [ ] Task executed fully (not described)
- [ ] Artifact inspected after creation
- [ ] Multi-perspective review completed
- [ ] Defects corrected
- [ ] Uncertainty disclosed
- [ ] Stopping conditions met

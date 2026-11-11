---
name: ultrawork
description: >
  Parallel execution engine for high-throughput task completion. Use when
  multiple independent tasks can run simultaneously via delegate_task,
  enabling concurrent agent execution and faster workflow completion.
  Best for research, code changes, and multi-file tasks that don't depend
  on each other.
version: 1.0.0
---
<Purpose>
Ultrawork is a parallel execution engine and execution protocol for independent work. It emphasizes intent grounding, parallel context gathering, dependency-aware task graphs for non-trivial work, and concise evidence-backed execution summaries. It is a component, not a standalone persistence mode — it provides parallelism and routing guidance, but not persistence, verification loops, or long-lived state management.
</Purpose>

<Use_When>
- Multiple independent tasks can run simultaneously
- User says "ulw", "ultrawork", or wants parallel execution
- You need to delegate work to multiple agents at once via delegate_task
- Task benefits from concurrent execution but the user will manage completion themselves
</Use_When>

<Do_Not_Use_When>
- There is only one sequential task with no parallelism opportunity — use a single agent call
- Task requires deep cross-cutting reasoning where parallel agents would conflict
- Tasks share mutable state and need sequential ordering
</Do_Not_Use_When>

<Why_This_Exists>
Sequential task execution wastes time when tasks are independent. Ultrawork enables firing multiple Hermes subagents simultaneously, reducing total execution time. It uses Hermes' built-in delegate_task tool (which supports parallel batch execution natively via the 'tasks' array parameter).
</Why_This_Exists>

<Execution_Policy>
- Fire all independent agent calls simultaneously using delegate_task(tasks=[...]) — never serialize independent work
- Batch size: delegate_task default max_concurrent_children is ~3. For workloads larger than that, run multiple sequential waves (3 tasks each) rather than trying to fire everything at once.
- Always provide rich context per task: include file paths, what to read first, the goal, and what output format you want. Subagents perform best when they don't have to ask clarifying questions.
- After delegate_task completes, re-read any files the parent read before editing — subagents may have modified them, and stale cached views cause "re-read before editing" warnings.
- For multi-wave work, use `todo` to track progress across waves. Update after each wave completes so the parent maintains context across long sessions.
- When using background processes (uvicorn, build watchers), prefer `uv run <command>` over sourcing venvs directly — the Hermes environment may have conflicting VIRTUAL_ENV bindings.
- First gather a directory listing (`ls -d`, `search_files`) to scope the work before deciding how to batch. Don't delegate exploration you could do in 2 seconds yourself.
- If user says a skill exists but you can't find it in skills_list: check the project's skills-lock.json for GitHub source references, then fetch the SKILL.md from raw.githubusercontent.com.
- Use background terminal (background=true, notify_on_complete=true) for operations over ~30 seconds (installs, builds, tests)
- ⚠ **Background output buffering pitfall:** Background terminals with inline Python scripts (`-c "..."`) or piped commands may produce ZERO output visible via `process.log()`. Always verify background progress by polling the effects (e.g., checking DB state, file output, or process `ps` status) rather than relying on output preview. For scripts that MUST produce visible progress, run in foreground with a timeout, or have the script write to a file you can tail.
- ⚠ **Background venv activation pitfall:** `source .venv/bin/activate && python3 ...` may not work correctly in background terminal commands — the shell state doesn't propagate. Prefer `.venv/bin/python3 ...` directly instead of activating the venv.
- Run quick commands (git status, file reads, simple checks) in the foreground
- Resolve intent and uncertainty before implementation; explore first, ask only when still blocked
- For non-trivial tasks, produce a dependency-aware plan with parallel waves before execution
- Keep delegated-task reports concise: short summary, files touched, verification status, blockers
</Execution_Policy>

<Steps>
1. **Ground intent first**: Confirm whether the request is implementation, investigation, evaluation, or research; do not code before that is clear
2. **Gather context in parallel**:
   - direct tools for quick reads/searches
   - exploration subagents for broad context
3. **Classify tasks by independence**: Identify which tasks can run in parallel vs which have dependencies
4. **Create a task graph for non-trivial work**:
   - Parallel Execution Waves
   - Dependency Matrix
   - acceptance criteria and verification steps per task
5. **Fire independent tasks simultaneously using delegate_task**: Pass all parallel-safe tasks in the 'tasks' array — they run concurrently
6. **Run dependent tasks sequentially**: Wait for prerequisites before launching dependent work
7. **Verify after each parallel wave**: Run lightweight verification (build check, test run, data integrity check) before launching the next wave. This catches integration errors early and prevents cascading failures.

⚠ **Verification trap: `tsc --noEmit` vs full build.** TypeScript's `--noEmit` flag uses a different compilation path than `tsc -b` (project build mode). In one session, `tsc --noEmit` passed cleanly while the full `npm run build` (which runs `tsc -b`) caught a JSX fragment nesting error. Always verify with the project's actual build command for code-change waves. For research/doc waves, lightweight checks suffice.
8. **Background long operations**: Builds, installs, and test suites use terminal(background=true, notify_on_complete=true)
9. **Verify when all tasks complete** (lightweight):
   - Build/typecheck passes
   - Affected tests pass
   - No new errors introduced
</Steps>

<Tool_Usage>
- Use `delegate_task(tasks=[...])` with multiple tasks in the 'tasks' array for parallel execution
- Use terminal(background=true, notify_on_complete=true) for long-running builds/tests
- Use foreground terminal for quick status checks and file operations
- Use read_file/search_files for direct context gathering instead of spawning subagents for simple reads
</Tool_Usage>

<Examples>
<Good>
Multi-wave batch: 13 projects analyzed across 4 waves (3 + 3 + 3 + 2):
```
# Wave 1: first 3 projects in parallel
delegate_task(tasks=[
  {"goal": "Analyze project A (claude-code)", "context": "Root: /path/to/A. Read CLAUDE.md, package.json, src/ structure first.", "toolsets": ["terminal", "file"]},
  {"goal": "Analyze project B (Engineering-Hub)", "context": "Root: /path/to/B. Read README, DEPLOYMENT.md, pubspec.yaml first.", "toolsets": ["terminal", "file"]},
  {"goal": "Analyze project C (FileDeleiveryRobot)", "context": "Root: /path/to/C. Read README.md, package.json, src/ dir first.", "toolsets": ["terminal", "file"]}
])

# Wave 2: next 3 projects
delegate_task(tasks=[...])  # next 3

# Wait for each wave to complete before launching the next
```
Why good: 3 tasks per wave respects concurrency limits. Each subagent gets rich context (what to read first, goal, expected output). Analysis is structured and comparable across projects.
</Good>

<Good>
Correct use of background execution:
```
terminal(command="npm install && npm run build", background=true, notify_on_complete=true)
delegate_task(tasks=[{"goal": "Update the README with new API endpoints", ...}])
```
Why good: Long build runs in background while short task runs via subagent.
</Good>

<Good>
Skill-finding workflow when user says a skill exists but you can't find it:
```
# 1. Check the project's skills-lock.json for the missing skill
read_file(path="~/project/skills-lock.json")
# 2. Found: ultrawork from yeachan-heo/oh-my-claudecode, path skills/ultrawork/SKILL.md
# 3. Fetch from GitHub
curl -sL "https://raw.githubusercontent.com/yeachan-heo/oh-my-claudecode/main/skills/ultrawork/SKILL.md"
# 4. Create globally with skill_manage(action='create')
```
Why good: Systematic recovery when skill_list doesn't show a known skill. Skills-lock.json is the project's truth for available skills.
</Good>

<Good>
Monolith refactoring across 5 parallel waves:
```
# Wave 1: Extract hooks (limit 3 per wave)
delegate_task(tasks=[
  {"goal": "Create useAuth hook from App.tsx", "context": "File: src/App.tsx. Auth logic lines 358-513. State: user, profile, loading. Flow: onAuthStateChanged → profile create/sync. Dev mode mock. Return: {user, profile, loading, handleLogin} Write to src/hooks/useAuth.ts", "toolsets": ["file"]},
  {"goal": "Create useCart hook from App.tsx", "context": "File: src/App.tsx. Cart lines 608-713, 2218-2357. State: cart, showCart, selectedWindow, checkoutError. Funcs: addToCart, removeFromCart, handleCheckout. Types from src/types.ts. Write to src/hooks/useCart.ts", "toolsets": ["file"]},
  {"goal": "Create useOrders hook from App.tsx", "context": "File: src/App.tsx. Data listeners lines 365-367, 475-594. State: orders, orgOrders, budget, restaurants, staffUsers. Firestore onSnapshot + dev mock. Write to src/hooks/useOrders.ts", "toolsets": ["file"]}
])

# Wave 2: Remaining hooks + first page
delegate_task(tasks=[...])  # useWallet, useOrg, HomePage

# Wave 3: Remaining pages
delegate_task(tasks=[...])  # WalletPage, BudgetPage, OrgPage

# Wave 4: Context + remaining components
delegate_task(tasks=[...])  # AppContext, ProfilePage, CartDrawer

# Wave 5: Rewrite App.tsx as slim shell
# (sequential — depends on all previous outputs)

# Then: build check → fix type mismatches → rebuild
```
Why good: Each wave's tasks are independent extracts from the same source. Rich context (file paths, line ranges, exact types, output paths) means subagents don't ask clarifying questions. The wave order respects dependencies (hooks → pages → context → shell). Build check after all waves catches integration errors.
</Good>

<Bad>
Sequential execution of independent work:
```
result1 = run task A  # wait...
result2 = run task B  # wait...
result3 = run task C  # wait...
```
Why bad: These tasks are independent. Running them sequentially wastes time. Use delegate_task(tasks=[A, B, C]) instead.
</Bad>

<Bad>
Delegating without context:
```
delegate_task(tasks=[{"goal": "analyze project X"}])
```
Why bad: Subagent has no starting point, wastes time discovering structure. Always include file paths, what to read first, and output format.
</Bad>
</Examples>

<Escalation_And_Stop_Conditions>
- When ultrawork is invoked directly, apply lightweight verification only — build passes, tests pass, no new errors
- If a task fails repeatedly across retries, report the issue rather than retrying indefinitely
- Escalate to the user when tasks have unclear dependencies or conflicting requirements
- For full persistence and comprehensive verification, consider sequential execution with careful checkpointing
- **Standing-goal spiral**: If you declare a goal complete and the same standing goal is auto-appended again (system-driven, not user re-request), DO NOT blindly continue. State explicitly what was done, note that you're blocked on specificity, and ask the user what specifically is missing. This avoids repeated wasted work cycles.
</Escalation_And_Stop_Conditions>

<Final_Checklist>
- [ ] All parallel tasks completed
- [ ] Build/typecheck passes
- [ ] Affected tests pass
- [ ] No new errors introduced

## Patterns

### Issue-Driven PM Loop
When the agent acts as a project manager tracking work through a Linear (or GitHub) board:

1. **Query the board** — pull open issues filtered by priority/phase
2. **Batch select ~3** — pick independent, well-scoped issues from different domains (backend, frontend, infra) to avoid merge conflicts
3. **Delegate in parallel** — fire all 3 via delegate_task(tasks=[...])
4. **Review output** — after wave completes, verify build/tests per issue
5. **Handle results**:
   - **Complete** → close the issue with a comment summarizing what was done
   - **Architectural/infra discovery** → flag as a NEW issue rather than letting the current task spiral
   - **Simple fixes** (syntax, small bugs) → fix directly instead of spawning new issues
6. **Repeat** — pick the next batch, don't spiral beyond 3 per wave

Sub-agents should NOT be asked to create Linear issues themselves. The parent (PM) owns the board. Include the project root path and relevant file structure in every task's context so sub-agents hit the ground running.

## Additional Pitfalls

### Subagent Workspace Isolation (ENVIRONMENT-DEPENDENT)

⚠️ **Behavior varies by environment.** In some Hermes Agent configurations (e.g., deepseek-v4-flash on OpenCode), sub-agents DO share the parent's filesystem and can write files that appear in `git diff` immediately. In others (Claude Code environments), sub-agents run in isolated sandboxes where file modifications do NOT propagate.

**How to determine:**
After delegate_task returns, run `git status` and `git diff --stat HEAD` immediately.
- **Files changed** → sub-agents share your filesystem. Your changes landed. Proceed directly.
- **Nothing staged** → sub-agents are sandbox-isolated. Apply changes manually (see below).

**When sandbox-isolated (three recovery approaches):**

1. **Re-apply from summaries (recommended for 3-5 files):** After delegate_task returns, read the detailed summaries, then re-apply each change manually in your own working directory using `patch`. This is the most reliable approach.

2. **Include absolute paths in task context:** If the parent has a known absolute path for the git repo (e.g., `/tmp/awun-compare`), include it in every task's context so the sub-agent patches the correct location. Only works if sub-agents share the parent's filesystem namespace.

3. **Clone fresh in sub-agent task:** Have the sub-agent clone the repo, make changes, and output diffs. Parent applies the diffs. Reliable but costs extra API time for the clone.

**Regardless of approach:** always `git status` and `git diff --stat HEAD` after delegate_task returns to verify changes landed. If nothing is staged, re-apply the changes manually.

**Lesson from AWUN (May 2026):** Three sub-agents completed AWU-15/16/17, each reporting test passes and providing detailed patch summaries. But the parent's `/tmp/awun-compare` git repo had **0 files changed** after delegate_task returned. Every single patch had to be re-applied manually by reading the sub-agent summaries and running `patch` commands. This cost ~15 minutes of rework. In a later session (same project, running as deepseek-v4-flash on OpenCode), sub-agents DID write files directly — always verify.

### Subagent Timeout
The 600s delegate_task timeout can kill tasks that are too broad. In one session, 2 of 3 delegated tasks timed out at 600s despite making 30+ API calls each. The sub-agents spent most of their time exploring/reading rather than editing.

**Mitigations:**
- Scope each task to a single deliverable, not "review and fix everything"
- Include specific file paths and entry points in the context to reduce discovery time
- For tasks needing many reads, split into two waves: investigation first, then implementation
- If a task times out, verify what WAS completed (check file timestamps, diffs) before re-delegating

## Reference Files
- `references/batch-project-analysis.md` — Proven patterns from a real 13-project analysis across 4 waves. Includes delegate_task context templates, batch sizing, scoping commands, and skill-recovery workflow.
- `references/cross-stack-multi-wave-execution.md` — 3-wave cross-stack pattern for backend + frontend + docs when task count exceeds concurrency limits. Includes wave ordering strategy, verification gates per wave, and a real AWUN project example.
- `references/research-synthesis-with-mcp.md` — Multi-source market research synthesis using parallel MCP tool queries. Includes cross-referencing workflow, competitor threat matrix template, market validation tables, and Nigeria-specific pitfalls.
- `references/project-readiness-audit.md` — Systematic production-hardening sprint: backend hardening (perf, security, validation), frontend optimization (performance, SEO, mobile), documentation, and data seeding — all executed in parallel waves across 3-5 phases. Covers wave planning, verification gates per wave, seeding strategies, and common pitfalls.
- `references/monolith-refactoring-with-ultrawork.md` — Decomposing a 2,300+ line React monolith into hooks, pages, context, and shell across 5 parallel waves. Covers context crafting for code-extraction tasks, wave planning, integration-error recovery, and offline-first patterns.
- `references/mikkies-2026-05-21-exa-research.md` — Real worked example of multi-source market research using parallel Exa MCP queries. Shows before/after findings table, document patch strategy, and pitfalls encountered during the Mikkies Cafe research session (May 2026).
- `references/data-parsing-quality-testing.md` — Two-phase validation methodology for CSV/Excel data parsing: schema inference accuracy + data type integrity. Covers Nigerian-vendor-specific edge cases (price ranges, emoji-laden titles, misspelled categories, text stock indicators, absurd multi-pack prices). Use during ultrawork verification waves that touch data ingestion.
- `references/awun-wave1-pm-loop.md` — Real AWUN PM loop example: 3 P3 issues across backend/API/frontend, 2 timeouts, partial recovery, Node version discovery
- `references/awun-pm-workflow.md` — AWUN-specific PM workflow: pick ~3 Linear issues per wave, delegate via ultrawork, review output, fix simple issues directly, flag architectural discoveries as new issues, mirror status to GitHub Projects board. Includes constraint propagation template and verification gate checklist.
- `references/awun-pm-workflow.md` — AWUN-specific PM workflow: Linear + ultrawork delegation patterns, wave batching rules, sub-agent config, verification gates, and pitfall knowledge from real production sessions.
- `references/appwrite-sdk-v18-patterns.md` — Appwrite Python SDK v18 Document/DocumentList structure, attribute async creation, 13-attribute free tier limit, repository pattern for provider abstraction, and common HTTP error codes. Discovered during parallel migration implementation.
</Final_Checklist>
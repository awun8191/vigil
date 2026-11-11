# Vigil Agent Compact Proposal v2.1 (revision B, 2026-11-08 PM)
# Reference: Hermes self model vs Vigil current implementation, grounded in code

Date: 2026-11-08
Sources inspected:
- `backend/src/modules/runs/core_context.py` (DEFAULT_BASE_IDENTITY, CORE_PHILOSOPHY_AND_BRAINSTORMING, build_core_system_prompt)
- `backend/src/modules/runs/tools/__init__.py` (TOOLS registry, TOOL_COUNT 11)
- `backend/src/modules/runs/tools/files.py, shell.py, search.py, skills.py, tasks.py, human.py, delivery.py`
- `backend/src/modules/runs/service.py` (create_run, spawn_child_run, _isolate_child_workspace, budget cascade)
- `backend/src/modules/runs/state.py` (MAX_RUN_DEPTH, ToolCall, PartitionPolicy, RunStateMachine)
- `backend/src/modules/runs/loop.py` (assemble, compact_check, infer, partition, dispatch, record)
- `backend/src/modules/runs/repository.py` (AgentConfigDTO: system_prompt, model, tools, skills, memory_scopes, approval_policy, limits)
- Hermes runtime: `~/.hermes/SOUL.md`, `~/.hermes/config.yaml` (agent.system_prompt, agent.personalities, prefill_messages_file, toolsets, delegation, compression, memory), `hermes-agent` skill, `local-assistant-tooling` skill

## 1. What Vigil has today

### 1.1 Identity compact (single layer)
`core_context.py:137-159` defines one static string:

- You are Vigil, autonomous software engineering, design, research agent
- Sandbox is `/workspace`
- Tools listed by name: `bash`, `read/write/edit`, `web_search/web_fetch`, `skill_load`, `manage_tasks`, deliver files
- Operational rules: greeting boundary, proactive clarification, non-interactive CLI flags, no foreground dev server, no emojis, ASD-STE100

`build_core_system_prompt()` prepends custom_instructions, appends Karpathy + brainstorming, appends active skills list. No personality overlay. No prefill examples. No per-config SOUL file.

`AgentConfigDTO.system_prompt` stores the rendered result. `get_or_create_default_agent_config()` in `service.py:401-419` seeds `vigil-default` once and never overwrites user edits. Good pattern, keep it.

### 1.2 Philosophy block (embedded, not loadable)
`CORE_PHILOSOPHY_AND_BRAINSTORMING` embeds:

- Karpathy 4 principles: Think Before Coding, Simplicity First, Surgical Changes, Goal-Driven Execution
- Streamlined brainstorming: Explore Context First, Proportionate Depth (Clear vs Creative builds), Isolation and YAGI
- ASD-STE100 + emoji restraint (20 word procedural limit, 25 word descriptive limit, active voice, controlled vocab)
- Skill catalog text for 5 bundled skills

This is Vigil equivalent of Hermes `agent.system_prompt`. Difference: Hermes keeps base identity in SOUL.md slot 1, config system_prompt slot 2, personalities slot 3. Vigil collapses all three into one Python constant. That makes per deployment persona edits require code change.

### 1.3 Tool registry (11 tools, stateless, approval leveled)
From `tools/__init__.py:174-184`:

- FILE_TOOLS (3): `read`, `write`, `edit`, level local, via SandboxRunnerProtocol, path confined to `/workspace`
- SHELL_TOOLS (1): `bash`, level local, 120s default, 256KB cap, no stdin, argv via runner, no shell
- DELIVERY_TOOLS (2): `deliver_file`, `generate_document` (markdown, text, html, csv), host delivery dir, artifact_ref path
- HUMAN_TOOLS (1): `ask_user`, level external, returns pending marker, non blocking, approval path owned elsewhere
- TASK_TOOLS (1): `manage_tasks`, pure function, caller passes full list, returns new list, MAX_TASKS 50
- SKILL_TOOLS (1): `skill_load`, level local, copies `/skills/<name>` to `/workspace/skills/<name>` via runner
- EXA_TOOLS (2): `web_search_exa`, `web_fetch_exa`, via Exa MCP HTTP, free endpoint plus optional EXA_API_KEY

Total 11. `TOOL_COUNT = 11` matches. Validation first via Pydantic, handler never raises, returns ToolResult(ok, content, artifact_ref, error).

Missing vs Hermes toolsets: no document reader (PDF, docx), no vision/image input, no session history search, no persistent memory read/write tool, no subagent delegate tool exposed to model, no browser automation, no code execution sandbox separate from bash.

### 1.4 Subagents (backend exists, model cannot use it)
`service.py:170-218` implements `spawn_child_run(parent_run_id, config_id, card_id, initial_prompt)`:

- Child depth = parent depth + 1, enforced by `MaxDepthExceeded` in `state.py:147-156`
- Fresh workspace ref `ws_<hex>` via `_isolate_child_workspace`, never shared with parent
- Queues `execute_run` job with `run_id`
- Cost cascade in `record_cost_micros`: child spend counts toward every ancestor budget, ancestor fails if subtree exceeds `limits.max_cost_micros`

Problem: no `Tool.define(name=spawn...)` in registry. Loop `partition` in `state.py:200-203` and `loop.py` knows local, background, external only. Model has no way to delegate. Compare Hermes:

- Hermes `delegate_task`: in conversation subagent, isolated context, subset of parent tools, bounded by parent loop, returns summary string. Used for quick parallel subtasks.
- Hermes `spawn hermes process`: fully independent OS process via `terminal(command="hermes chat -q ...")` or tmux PTY for interactive. Separate session, full tools, hours or days. Used for long missions.

Vigil already has the backend equivalent of Hermes spawn (separate run row, separate workspace, separate job). It lacks the model facing delegate entry point and the parent resume wiring.

### 1.5 Context assembly and compaction
`loop.py:199-249` `build_model_messages`: system prompt first, then one tool group per assistant turn in call order, orphan tool rows dropped for Anthropic compliance. `estimate_tokens`: 4 chars per token plus constant. `compact_check` stage triggers `CompactionProvider.compact()`.

Hermes equivalent: prompt caching aware (no mid conversation toolset change), compression with threshold 0.35, target 0.15, protect_first_n 1, protect_last_n 8, hygiene_hard_message_limit 200. Vigil `context_token_limit 100000`, `compact_ratio 0.7` in `RunLoop.__init__`. Vigil keeps full tool outputs in messages table plus artifacts store, compaction summarizes. Same shape, different tuning.

Hermes memory three tier: native skeleton every turn, CLAUDE.md hot cache, memory files deep storage on demand, plus `memory` toolset and `session_search`. Vigil `AgentConfigDTO.memory_scopes: list[str]` exists but no memory tool in registry. Scopes are declared, not usable.

## 2. What Hermes does that Vigil should adopt

### 2.1 Three layer identity, not one constant
Hermes order:
1. SOUL.md (base identity, slot 1)
2. `agent.system_prompt` (deployment override, slot 2)
3. `agent.personalities` preset (session overlay, slot 3) plus `prefill_messages_file` (few shot conditioning)

Proposal for Vigil: split `core_context.py` into three render inputs without breaking stored configs:

- `base_identity.md` file (product SOUL): Vigil who, workspace rule, greeting rule. Versioned in repo, loaded at seed time.
- `config.system_prompt` delta: team custom instructions, prepended in `build_core_system_prompt(custom_instructions)`. Already supported, keep.
- `config.personality`: optional named overlay map (`concise`, `technical`, `teacher`, etc). Append one selected overlay at render time. New column or `limits` extension, default empty.
- `prefill.json`: optional example exchanges injected after system prompt every turn, not logged to sessions. Needed for ask_user discipline and deliver_file discipline.

Benefit: persona change is config edit, not code deploy. Matches Hermes practical rule: always on behavior in system_prompt, switchable mode in personalities.

### 2.2 Tool gaps, ranked by need
From your prompt (documents, search) plus code comparison:

1. `read_document`: PDF, docx, xlsx, pptx text extraction. Hermes pattern: user upload lands in cache dir, `read_file` shows binary garbage, use `fitz` PyMuPDF via code path. Vigil `read` returns raw bytes decoded as utf8 with replace, so PDFs return mojibake. Add dedicated tool that detects mime, extracts text plus page markers, caps chars, returns artifact_ref for full text. Keep `read` for plain text only.
2. `web_search` + `web_fetch` already exist via Exa. Keep, but add `objective` guidance in system prompt: search with semantically rich description, then fetch 2 to 4 urls. Current Exa args already support `objective` and `numResults`. Surface that in prompt, it is underused.
3. `memory_search` + `memory_store`: implement against existing `memory_scopes`. Read path uses pgvector + time decay (ARCHITECTURE.md objective). Write path stores fact with scope, run_id, session_id. Without this, scopes are dead config.
4. `delegate_subagent` (model facing): thin wrapper over `spawn_child_run`. This is the highest leverage addition. See section 3.
5. `session_search`: list prior runs in same session_id, reuse `list_session_history` already referenced in `loop.py:434-438`. Model needs it to avoid rework after compaction.
6. Defer: browser automation, image_gen, TTS. Not needed for Vigil core (engineering, design, research). Design skill already tells model to use lucide icons, not emojis. Vision input can come later with artifact images.

Keep `local-assistant-tooling` structural guarantees Vigil already follows: list argv only never shell string, path confinement double checked (tool + runner), bounded timeouts and caps, no write capability where not needed, audit every action to run_steps journal. Vigil tools already do this well. Do not regress.

### 2.3 Personality and writing standards
Vigil embeds ASD-STE100 + Karpathy + brainstorming. Good. Hermes adds `verify_on_stop`, `parallel_tool_call_guidance`, `task_completion_guidance`, `environment_probe`, `coding_context: auto`.

Adopt three small additions to base identity:

- Parallel calls: independent reads batched in one turn, dependent calls serialized. Hermes does this via guidance flag. Vigil loop already supports parallel tool_calls per assistant turn, but prompt never says so.
- Verify on stop: before final answer, list files changed, tests run, verification output. Vigil Goal Driven Execution says loop until verified, but no explicit stop checklist.
- Environment probe: first turn may run `bash(pwd, ls)` + `read` key configs before planning. Vigil Explore Context First says this, enforce it as step 1.

## 3. Subagent management design (detailed)

### 3.1 Hermes behavior to copy
- Children know nothing of parent conversation except what parent passes in `context` + `goal`. Self contained prompt required.
- Child summaries are self reports, not verified facts. Parent must verify side effects by reading back target (file, url, run row) before claiming success.
- Max 3 concurrent, one line status, take over if stuck 2 min (your memory rule). Hermes has no hard cap in code, but your ops rule does. Encode it in tool policy.
- No nested delegate chains without bound. Hermes uses `max_iterations 50` + `goal_max_turns 20`. Vigil has `MAX_RUN_DEPTH` + `max_iterations 40`. Keep both.

### 3.2 Proposed `delegate_subagent` tool spec
Level: `background` (not local, not external). Partition routes to job queue, run pauses to `awaiting_background_job`, resumes on child completion. Reuses existing `BACKGROUND_JOB_TYPE = execute_run` comment in `loop.py:94-100`.

Args model:

- `goal: str` (required, 1..2000 chars): verifiable outcome, e.g. Research Exa vs Tavily pricing, return table with urls.
- `context: str` (required): files, ids, constraints the child needs. No implicit parent access.
- `config_name: str | None`: child agent config, defaults to parent config. Allows researcher vs coder split later.
- `budget_micros: int | None`: child subtree cap, defaults to parent remaining.
- `timeout_seconds: int` default 600, max from settings.

Handler (service layer, not sandbox):

- Calls `spawn_child_run(parent_run_id, config_id, initial_prompt=f"Goal: {goal}\nContext: {context}")`
- Calls `_isolate_child_workspace(parent_id, child_id)` (exists but currently unused in `spawn_child_run`, wire it)
- Appends opening user message, enqueues job, returns `{child_run_id, status: queued}`
- Loop `dispatch` marks parent `awaiting_background_job`, `record` persists step. On child terminal state, parent resumes with child summary injected as tool result (child final answer + file list + cost).

Policy (encode in `PartitionPolicy` + prompt):

- Max 2 active children per parent (revised per your cap). Third delegate call fails with `too_many_children`, model must wait or merge goals.
- Max depth from settings `MAX_RUN_DEPTH` (already in `settings.py:164`). Depth exceeded fails fast, no enqueue.
- No child may call `delegate_subagent` past depth limit. Depth check is the recursion guard.
- Parent must verify: after child completes, parent reads claimed files via `read` before final answer. Write this into tool description so model does it.
- Cost counts to parent subtree (already implemented in `record_cost_micros`). No extra work.

This gives Vigil Hermes parity: quick subtasks via delegate tool, long missions via existing top level runs API, both isolated.

### 3.3 Alternatives considered and rejected
- Expose raw `spawn_child_run` RPC to model without prompt wrapper: rejected, leaks run_ids and config_ids, no goal verification.
- Shared workspace between parent and child: rejected, causes git and file races. Hermes `-w` worktree mode exists for same reason. Keep `ws_<hex>` isolation.
- Synchronous in loop execution (run child loop inline): rejected, blocks parent iterations, breaks `max_iterations` accounting, loses job retry. Use background job path already designed.

## 4. Proposed compact text changes (minimal diff)

Keep `DEFAULT_BUNDLED_SKILLS` and `CORE_PHILOSOPHY_AND_BRAINSTORMING` intact. Change `DEFAULT_BASE_IDENTITY` and `build_core_system_prompt` signature:

- Add `personality: str | None` param, append overlay from `PERSONALITIES` map if set.
- Add parallel tool guidance paragraph (2 lines).
- Add stop checklist paragraph (3 lines: changed files, tests, verification).
- Add delegate policy paragraph (max 2 children, goal is required and verified, context is optional helper).
- Add document handling paragraph (use `read_document` for PDFs, never `read` raw PDF).
- Mention `memory_search` when `memory_scopes` non empty.

No code deletion. Fix forward, per your scope rule. `TOOL_COUNT` becomes 14 (add `read_document`, `delegate_subagent`, `memory_search` + `memory_store` counted as 2, or single `memory` tool with operation enum like `manage_tasks`). Prefer single `memory` tool with `operation: store, search` to keep registry small, matching `manage_tasks` pure function style.

## 5. Implementation plan (actionable, test backed)

1. Identity split: extract `DEFAULT_BASE_IDENTITY` to `backend/src/modules/runs/identity.md`, add `PERSONALITIES` map + `build_core_system_prompt(..., personality)` + test that seed does not overwrite edited `vigil-default`.
2. `read_document` tool: mime detect, `pypdf` or `fitz` extract, char cap + artifact_ref overflow, tests for pdf, docx missing dep failure message, path escape refusal.
3. `memory` tool: wire to existing memory module + pgvector, scopes enforced, tests for scope isolation.
4. `delegate_subagent` tool: `Tool.define(level=background)`, service wiring, workspace isolation call, depth + fanout 2 enforcement, parent resume injection, e2e test: parent delegates research, child writes file in own workspace, parent verifies via explicit read, budget cascade asserted.
5. Prompt update: parallel guidance, stop checklist, delegate policy, document routing. Update `service.py:407-415` docstring and `TOOL_COUNT`.
6. `try_agent.py`: add CLI demo for delegate (print child run id, poll, show resume). Verifies policy without UI.

Acceptance: `uv run pytest` passes, `TOOL_COUNT` updated, e2e delegate cycle completes, compaction preserves pending delegate result (same guarantee as approval survival test in `tests/e2e/test_compaction_cycle.py`).

## 6. Comparison table (Hermes vs Vigil, for quick reference)

| Concern | Hermes (this agent) | Vigil today | Proposal |
|---|---|---|---|
| Base identity | SOUL.md slot 1 | DEFAULT_BASE_IDENTITY constant | Extract to identity.md, same content |
| Deployment override | agent.system_prompt slot 2 | custom_instructions param | Keep, document as slot 2 |
| Switchable persona | agent.personalities + /personality | None | Add PERSONALITIES map, config selected |
| Few shot conditioning | prefill_messages_file | None | Add optional prefill.json |
| File read/write | file toolset | read, write, edit local | Keep, add read_document |
| Shell | terminal backend local/docker/ssh | bash via SandboxRunner, 120s, 256KB | Keep, no change |
| Web | web search + extract | web_search_exa + web_fetch_exa | Keep, prompt objective usage |
| Skills | skills hub + load | skill_load local | Keep, 5 bundled |
| Tasks | todo toolset | manage_tasks pure function | Keep |
| Human | clarify tool | ask_user pending marker | Keep |
| Deliver | messaging + artifacts | deliver_file + generate_document | Keep |
| Memory | memory tool + session_search + 3 tier | scopes declared, no tool | Add memory tool + session_search |
| Subagents | delegate_task + spawn process | spawn_child_run backend only | Add delegate_subagent background tool, max 2, depth capped, goal required, verify after |
| Context | compression threshold 0.35 target 0.15 protect 8 | compact_check ratio 0.7 limit 100k | Keep loop, tune after delegate e2e |
| Safety | approvals mode, redact_secrets, allowlist | approval levels read/local/external, path confinement, caps | Keep, no regression |
| Verification | verify_on_stop, parallel guidance | Goal Driven Execution text | Add stop checklist + parallel paragraph |

## 7. Revision B: documents first, delegation capped at 2, grounded in code

### 7.1 Document ability is core, not extra (read, create, send, render)

Current Vigil path, confirmed in code:

- `tools/files.py:61-69 read`: calls `runner.read_file`, decodes bytes as utf8 with replace. A PDF therefore returns mojibake. No mime check.
- `tools/delivery.py:124-148 deliver_file + generate_document`: reads workspace bytes via runner, writes to `context.delivery_dir` on host with `_leaf` escape guard, returns `ToolResult.success(summary(path,name,size,sha256), artifact_ref=host path)`. Formats today: `FORMAT_EXTENSIONS = markdown .md, text .txt, html .html, csv .csv`. No docx, no xlsx, no pdf render.
- `modules/artifacts/service.py:121-150 get_artifact_stream`: streams 64 KiB chunks, filename from `artifact.path`, but `media_type` is hardcoded `application/octet-stream`. `data.py:8-14 ArtifactCreateRequest kind = document, diff, log, export`, with `preview` 500 chars. So backend stores and streams, but frontend cannot pick a viewer by mime.
- `modules/chat/service.py:85-102 send_message`: persists user turn only. Reply comes from `POST /runs` with thread as `session_id`, RunLoop persists replies into same messages table. `MessageItem.tsx:59-72` already pulls `useRunSteps(run_id)` for thinking trace. No artifact card yet.
- `web/src/components/chat/MarkdownContent.tsx:98-100`: `ReactMarkdown + remarkGfm + remarkMath + rehypeKatex`, custom `CodeBlock`. Markdown renders well already. Missing is the artifact attachment renderer.

Required document set for revision B:

1. `read_document(path, max_chars)`: mime sniff by extension + magic bytes. md/txt/csv pass through to `read`. pdf via `pypdf` text extract with page markers `[p1]`, xlsx via `openpyxl` sheet by sheet with row caps, docx via `python-docx` paragraph + table extract. Returns truncated text + `artifact_ref` to full extracted text. Failure mode is explicit: missing dep returns `install pypdf/openpyxl/python-docx` message, never raises across `Tool.invoke` boundary (`tools/__init__.py:132-150`).
2. `generate_document` extension: keep existing 4 formats, add `docx` and `xlsx` render paths. docx built with `python-docx` with heading, normal, table styles so output is well built, not plain dump. xlsx built with `openpyxl` with header row bold, freeze panes, auto filter, column widths. pdf stays as export via `generate_document format=html` then frontend print to pdf, or add `weasyprint` later. Do not add pdf generation in v1, keep scope tight.
3. Send to chat interface: every `deliver_file` and `generate_document` call must also call `ArtifactsService.save_artifact(run_id, kind=document, path, size, preview)` so `list_artifacts_by_run` returns it. Today delivery writes to host dir but never registers the row. That is why chat cannot show it. Fix is one call in the executor layer that owns run_id, not in the tool handler that only has ToolContext.
4. Render on chat interface: new `ArtifactCard.tsx` next to `MessageItem.tsx`. Behavior by mime from new `media_type` mapping (fix hardcoded octet stream):
   - md: rendered with existing `MarkdownContent`, collapsed to 12 lines with Expand/Collapse toggle, expand opens full width modal with same renderer.
   - pdf: iframe `src=/artifacts/{id}/stream` with page nav, zoom in/out, expand button opens large modal. Use native browser pdf viewer first, no custom pdf lib in v1.
   - docx: v1 shows preview text + Download + Open flow (convert server side to html for preview via `mammoth` or `python-docx to html`). Do not try to render docx natively in React.
   - xlsx/csv: table preview first 20 rows with horizontal scroll, expand opens full grid modal with sticky header, sheet tab switch if multi sheet.
   All cards show name, size, sha256 short, Download button hitting the stream endpoint.

Acceptance for docs: agent writes `report.md`, `plan.docx`, `budget.xlsx`, `paper.pdf` extract via `read_document`, delivers all four, each appears as a card in thread, md expands inline, xlsx paginates, pdf zooms.

### 7.2 Goal is required, context is optional helper (delegate schema change)

Your correction adopted: goal carries the contract, context is best effort helper.

Hermes grounding, actual files:

- `tools/delegate_tool.py:1-11` docstring: child gets fresh conversation, own task_id (terminal session, file-ops cache), parent toolsets minus blocked, focused system prompt built from goal + context. Parent only ever sees delegation call + summary result, never child intermediate calls.
- `tools/delegate_tool_child_run.py:_build_child_goal_message`: builds child opening user message from goal + context. If context is empty, goal alone still forms a valid prompt. That is the pattern to copy.
- `tools/delegate_tool_tasks.py`: normalizes task list, each task needs `goal`, `context` optional. Batch mode runs tasks in parallel via `DaemonThreadPoolExecutor(max_workers=batch.max_children)` in `tools/delegate_tool_dispatch.py:148-183 _run_children_parallel`, results sorted by `task_index` on return.
- `tools/delegate_tool_config.py:19 _DEFAULT_MAX_CONCURRENT_CHILDREN = 10`, `23 MAX_DEPTH = 1 flat by default`, `87-92 _get_oneshot_max_children default 2`. Hermes default concurrency is 10, one shot default is 2. Your cap of 2 matches Hermes one shot default exactly.
- `tools/delegate_tool_toolsets.py:14-22 DELEGATE_BLOCKED_TOOLS = delegate_task, clarify, memory, send_message, cronjob_manage`, `23 DEFAULT_TOOLSETS = terminal, file, web`. `_resolve_child_toolsets` intersects requested toolsets with parent composite expanded set, child never gains a tool parent lacks. Orchestrator role re-adds `delegation`, leaf cannot recurse.

Vigil mapping, actual files:

- `backend/src/modules/runs/service.py:170-218 spawn_child_run`: takes `parent_run_id, config_id, card_id, initial_prompt`, creates child `RunCreateDTO(parent_run_id=parent.id, status=queued)`, appends one user message, enqueues `execute_run` job. `_isolate_child_workspace` exists at `:220-230` but is never called from `spawn_child_run`. Must wire it.
- `backend/src/modules/runs/state.py:147-156 MaxDepthExceeded`, `state.py:438-441 list_child_runs`, `service.py:300-332 _ancestor_ids + _subtree_cost_micros + budget fail`. Cost cascade already correct.
- `backend/src/modules/runs/loop.py:273-308 DispatchOutcome(executed, enqueued, awaiting, approvals, jobs)`, partition policy decides local vs background vs external. New tool must be `level=background` so dispatch enqueues job and sets parent `awaiting_background_job`, then resumes on child terminal state.

New `delegate_subagent` args for Vigil (cap 2):

- `goal: str` required, 1..2000 chars. The verifiable outcome. Rendered as `Goal: {goal}` first line of child opening message.
- `context: str | None` optional. File paths, ids, constraints. Rendered as `Context: {context}` second block, omitted when empty.
- `config_name: str | None` optional, defaults to parent config.
- `timeout_seconds: int` default 600.
- Enforcement: `list_child_runs(parent).active count >= 2` returns `ToolResult.failure(too_many_children: parent already has 2 active children)`. Depth check via `MAX_RUN_DEPTH` raises `MaxDepthExceeded` before enqueue.

Prompt text for tool description: Goal is the contract and is required. Context is a helper and may be empty. Child knows nothing except goal + context. Parent must verify claimed files with `read` or `read_document` before final answer.

### 7.3 What changed from v2 to v2.1

- Delegation fanout 3 changed to 2 everywhere (policy, prompt, plan, table).
- Goal promoted to required contract, context demoted to optional helper, matching Hermes `_build_child_goal_message` behavior.
- Documents expanded from one line (`add read_document`) to full pipeline: read extractors, docx/xlsx generation with styling, artifact row registration, mime mapping fix, ArtifactCard viewer spec per format with expand behavior.
- Code grounding added with file and line pins so implementation can proceed without re-discovery.

End of revision B. No code changed in this pass. Next on your go: implement in order 1) artifact registration + mime fix, 2) read_document, 3) docx/xlsx generation, 4) ArtifactCard viewer, 5) delegate_subagent with cap 2.

## 8. Revision C: general assistant identity, new skills vendored, steering model

### 8.1 Identity correction: proactive assistant, not code agent

Changed `backend/src/modules/runs/core_context.py:145-149 DEFAULT_BASE_IDENTITY` from software engineering agent to:

- You are Vigil, an autonomous, proactive AI assistant. You help with everyday tasks, research, planning, writing, decisions, and building things, including software, documents, and analysis.

Implemented in this pass, not just proposed. `DEFAULT_BUNDLED_SKILLS` grew 5 to 8. Catalog text in `CORE_PHILOSOPHY_AND_BRAINSTORMING` now lists verification and decision skills alongside design and literature skills. Design remains a capability, not the definition.

Proactive rules to add to prompt next (not yet coded, needs your go):

- If a run has no user reply for N hours and a background job completes, send a status nudge with artifact links, not silence.
- If inbox, calendar, or watched dir changes, open a run with summary + proposed next step, ask to proceed unless approval policy allows auto act.
- Never present a plan without offering to execute the first safe step.

### 8.2 Skills vendored from Hermes toolkit (done, on disk)

Copied whole directories with `cp -r` so `skill_load` works unchanged:

- `backend/src/modules/skills/bundled/plus-ultra/` (32K, SKILL.md 346 lines + references/schedulepage-double-fetch-migration-bug.md). Source: `~/.hermes/profiles/analyst/skills/plus-ultra`. Anti-laziness loop: intent reconstruction, assumption audit (known, verified, reasonable but unverified, materially uncertain, unsafe), verify when it matters, tools over pretending, execute fully, inspect artifacts, multi perspective review, correction loop, disclose uncertainty.
- `backend/src/modules/skills/bundled/ultrawork/` (148K, SKILL.md + 12 references including batch-project-analysis, multi-wave execution, research synthesis). Source: analyst `software-development/ultrawork`. Parallel engine: ground intent, gather context in parallel, classify independence, task graph with waves of max 2 for Vigil (Hermes default wave 3, Hermes cap 10, Vigil cap 2), fire via `delegate_subagent`, verify after each wave. This is the ultra thing you named.
- `backend/src/modules/skills/bundled/marketing-psychology/` (28K, SKILL.md). Source: analyst `marketing-psychology`. Mental models for decisions: first principles, jobs to be done, inversion, Pareto, constraints, opportunity cost, second order thinking, attribution, mere exposure, availability, anchoring, social proof, scarcity, loss aversion, framing, nudge. Used when choice involves time or money, or why people buy.

Why these three: plus-ultra is the quality gate you invoke most (fact 60), ultrawork is the parallelism protocol that matches delegate cap 2 waves, marketing-psychology is the decision layer a general assistant needs beyond code. karpathy-guidelines stays embedded, no copy needed. Commonly used runners up for later: `investigate` (root cause, four phases), `document-generate` (Diataxis), `context-save/restore` (your save progress pattern).

Verify with: `skill_load(name=plus-ultra)` then `read(path=/workspace/skills/plus-ultra/SKILL.md)`. Same for ultrawork, marketing-psychology. Registry picks them up automatically because `locate_skill_dir` falls back to `BUNDLED_SKILLS_ROOT` in `backend/src/modules/skills/service.py:108-118` and lists bundled in `:521-524`.

### 8.3 Steering and context injection: how Hermes does it, what Vigil lacks

Hermes code pins:

- `agent/interrupt_control.py:163 interrupt(), :310 steer(text, ticket), :398 _drain_pending_steer()`: steer appends `(ticket, text)` under `_pending_steer_lock`. Drain happens at next iteration boundary, never mid tool.
- `agent/agent_runtime_helpers.py:3668 apply_pending_steer_to_tool_results`: injects steer text into last tool result slot, the only role alternation safe slot (`prompt_builder.py:558` comment). `turn_iteration_prep.py:178-179` drains pre API steer without breaking prompt cache.
- `tools/delegate_tool_registry.py:124-156 steer_subagent, :92-104 interrupt_subagent, :173-183 list_active_subagents, :198-306 control plane list/steer/stop`: parent may steer or stop own children and grandchildren only (`_owns_subagent_record`, `_is_descendant_of`). Steer queues without stopping, stop is cooperative interrupt, missed steer lands as `missed_steer` in completion entry.
- Context injection layers: SOUL slot 1, system_prompt slot 2, personalities slot 3, `prefill_messages_file` few shot every turn, native memory Tier 0 every turn, CLAUDE.md Tier 1 hot cache, memory files Tier 2 on demand, `compression_marker elide`, live transcript pinning via `_parent_live_home`.

Vigil code pins:

- No steer slot. Only `service.py:576-583 resume_run` (re process waiting run) and `:585-625 cancel_run + _cancel_one` (fails run + every descendant via `_descendant_ids`, appends `run_failed reason cancelled`). Cancel is destructive, kills parent and children, no soft redirect.
- Loop `loop.py:847-957` cancels streaming producer on exit, `stream.py:41` SSE resume via Last-Event-ID, `state.py:788` rows persist so restart keeps history. Safe resume exists, safe steer does not.
- Context injection today: `build_core_system_prompt` static, `loop.py:430-438 prior_history via list_session_history`, `compact_check` summarizer. No pending steer table, no personalities, no prefill.

Proposal for Vigil safe steering (needs backend change, not in this pass):

- `POST /runs/{id}/steer {message}`: appends `messages(role=user, content=[steer] message)` OR separate `pending_steer` column drained in `loop.py assemble` stage and injected as user follow up. Drained once per iteration, same as Hermes. Ownership check: only session owner, and for child runs only parent session owner (mirror Hermes `_owns_subagent_record`).
- `POST /runs/{id}/interrupt {mode: soft, hard}`: soft sets `interrupt_requested` flag checked at `partition` and `dispatch` boundaries, current tool finishes, next iteration sees steer. Hard cancels current `infer` streaming call (asyncio cancel in `_infer_streaming`), records step, keeps all rows so `resume_run` continues without losing initial context. Never delete messages.
- `GET /runs/{id}/children` + control plane: list active children (from `list_child_runs` filtered non terminal), steer child, stop child. Cap 2 enforced here too.
- UI: ThinkingCapsule gets Steer input + Interrupt button while `status=running`. Steer sends without stopping stream, interrupt soft first, hard on second press. Same pattern as Hermes gateway interim messages.

Until that ships, safe workaround today: use `cancel_run` only when you truly want to kill, otherwise send a normal chat message in same session thread and let next run pick it up via `list_session_history`. Do not kill a run to correct it, you lose the in flight model call but rows remain, resume keeps context.

End of revision C. Code changed in this pass: identity line, bundled skills 5 to 8, catalog text. Doc updated. Next on your go: steer/interrupt endpoints + ArtifactCard viewer + delegate_subagent cap 2.

## 9. Revision D: memory that the agent can actually use

### 9.1 Problem, confirmed in code

Vigil has a real vector memory backend with no agent facing handle:

- `modules/memory/service.py:258-410 index_session`: background job chunks user and assistant rows into windows capped at 512 tokens (`chunker.py:9`), embeds locally with bge-small-en-v1.5 384 dim int8 ONNX (`settings.py:100-128 EMBEDDING_MODEL, EMBEDDING_DIMENSIONS 384, EMBEDDING_MAX_TOKENS 512, batch 4, 1024 tokens, RSS under 350 MB`), stores Postgres pgvector HNSW `ix_memory_chunks_embedding_hnsw` (`retrieval.py:71`), with `scope` global, card, agent, `summary`, `source_message_ids`, `embedding_model`, `excluded` flag (`repository.py:10-34`).
- `service.py:566-578 search_memory`: encodes query on local model, returns decayed ranking. Retrieval policy from settings: `MEMORY_RETRIEVAL_HALF_LIFE_DAYS 30.0, FLOOR 0.35, OVERRIDE_THRESHOLD 0.65, MIN_SIMILARITY 0.25, CANDIDATE_K 50, TOP_N 8, EF_SEARCH 200` (`settings.py:130-139`). Correct answers measured 0.679 to 0.801, best wrong 0.561 to 0.615, hence override 0.65.
- `router.py:34-43 build_memory_router`: `GET/POST /memory/search`, `GET /chunks/{id}`, `PATCH exclude`, `DELETE chunk`. All behind `get_current_user`. Human API only.
- `runs/repository.py AgentConfigDTO.memory_scopes: list[str]` exists, but `runs/tools/__init__.py` has no memory tool. The model can neither query (`search_memory`) nor store (only passive `index_memory` job on idle 900s, batch 200 in `service.py:36-43`). So memory is write only by the indexer, read only by the dashboard. That is the gap you named.

Hermes contrast, actual files:

- `tools/memory_tool.py:222 memory_tool(action, target, content, operations)`: actions add, patch, remove, batch atomic against final budget, write gate plus background delete gate. Store is on disk markdown (`get_memory_dir`, `load_on_disk_store`), not vectors. Model writes directly.
- Native memory Tier 0 `~/.hermes/memories/MEMORY.md` injected every turn, Tier 1 `CLAUDE.md` hot cache, Tier 2 `memory/` files on demand. Plus holographic `fact_store`: add, search, probe, reason, with trust scores and entity index. Query based, so no hard item cap needed on facts. Caps live on the injected tiers (char budgets, `max_bytes 50000, max_lines 2000` in tool output config), not on the store.

### 9.2 Decision: both layers, not one

Adopt your cheap replication split:

1. Layer A agent owned scratch file (markdown, capped). This is the dedicated memory file the agent stores by itself. No vectors, no DB, survives compaction because it is re injected as system context.
2. Layer B existing pgvector semantic memory (uncapped items, capped retrieval). This is the holographic equivalent. No item cap needed because retrieval is `CANDIDATE_K 50` then decay rank then `TOP_N 8`. Cap the query, not the store.

Why markdown for Layer A and not JSON: the agent reads and edits it with existing `read`, `write`, `edit` tools, renders in chat with existing `MarkdownContent`, diffs cleanly in git, and matches Hermes Tier 0 which is also markdown. JSON adds parse failure modes for zero retrieval gain at this size. Keep one JSON sidecar only if you later need machine fields (ids, timestamps), but source of truth stays markdown.

### 9.3 Layer A spec: `MEMORY.md` per agent config, capped

- Location: one file per agent config, stored as `memory_file` text on `AgentConfigDTO` (new nullable text column) plus mirrored to workspace as `/workspace/MEMORY.md` at run start so `read` works offline. Human editable in settings UI.
- Caps (hard, enforced in tool, not prompt): max 100 items (lines starting with `- ` or `##`), max 200 chars per item, max 12000 chars total file. These numbers fit every turn injection without blowing `context_token_limit 100000`. Rationale: 100 x 200 = 20000 worst case, trimmed to 12000 by recency, about 3000 tokens, acceptable as system suffix.
- Tool `memory_file` with `operation: read, add, patch, remove, prune` (pure function style like `manage_tasks`, caller passes nothing, server owns file, unlike tasks which are client passed). Add appends one item, patch replaces by index or exact old_string once (same uniqueness rule as `edit`), remove deletes, prune drops oldest beyond 100. Every op returns `{items, count, chars, truncated}` so the model sees the cap honestly (per local-assistant-tooling rule: never claim a total you do not know).
- Injection: `build_core_system_prompt(..., memory_text)` appends `## Agent Memory (persisted by you, max 100 items)` suffix after skills list, before personality overlay. Also included in `estimate_tokens` via normal message path. Compaction must preserve it: add to barrier check same as approvals (your `test_approval_survives_compaction` pattern).
- What goes in it: call information the agent decides to remember by itself. Examples: user prefers csv over xlsx, vendor fee 4.5 percent, project root paths, doc style rules, open approval ids. Never secrets, tokens, or full file contents. One fact per line, dated `[2026-11-08]`.
- Acceptance: add 101st item drops oldest, 201 char item rejected with `item too long`, file renders in chat settings page, survives compaction e2e.

### 9.4 Layer B spec: expose the vectors the agent already has

- New tools reusing existing service, no new tables:
  - `memory_search(query, scopes, top_n)`: thin wrapper over `MemoryService.search_memory(user_id, query, scopes, top_n)`. Level `read`. Returns `{results: [{content, summary, similarity, age_days, final_score}], count}`. Default `top_n 8`, max 20. Scopes default from `config.memory_scopes`, model cannot escalate to other user ids (principal scoping per your Firebase rule).
  - `memory_store(content, scope)`: explicit remember path the indexer lacks. Validates scope in `global, card, agent`, embeds via same local embedder, calls `save_chunk` with `source_message_ids` of current run. Level `local`. Rate limit: max 20 stores per run, max 2000 chars per content, to prevent D1 style burn loops (your Cloudflare lesson applied here: plan writes to fit quota, never blind loops).
- No item cap on Layer B. Reasons: HNSW handles scale, `CANDIDATE_K 50` plus decay plus `TOP_N 8` bounds every read, `excluded` flag plus `DELETE chunk` gives correction without deletion pressure, embedding model gate (`retrieval.py:161-167` mismatched model never returns) prevents cross model pollution. Add per scope soft quota alert at 10000 chunks (log, not block) if you want cost visibility, but do not fail stores.
- Prompt text: when `memory_scopes` non empty, append `You have long term memory. Use memory_search before asking the user for something they already told you. Use memory_store when the user says remember, prefers, always, never, or when a fact will matter next session.`
- `TOOL_COUNT` becomes 11 + read_document, delegate_subagent, memory_search, memory_store, memory_file = 16. Or fold search+store into one `memory` tool with `operation` enum to keep 15. Prefer two tools for clarity because read vs write have different approval levels (`read` vs `local`).

### 9.5 How this replicates holographic memory on the cheap

| Hermes holographic | Vigil cheap equivalent | Why it holds |
|---|---|---|
| fact_store add/search/probe/reason, trust, entities | Layer B pgvector search_decay_weighted + scopes + half life 30d + override 0.65 | Same query shape: semantic similarity then recency boost, top_n bound. Entities become `scope` + text search, no separate index needed at this scale |
| Tier 0 MEMORY.md every turn | Layer A MEMORY.md suffix in system prompt, 100 items, 12000 chars | Same injection guarantee, same markdown edit path, capped so prompt cache stays stable |
| Tier 1 CLAUDE.md hot cache | `custom_instructions` + personality overlay (section 2.1) | Same config edit path, not per run |
| Tier 2 memory files on demand via memory tool | `memory_search` + `read_document` on artifacts | Same on demand load, vectors replace file scan |
| Caps on injection, not store | Caps on Layer A file + Layer B top_n, not chunk count | Same principle you stated: query directly, so store needs no cap |

End of revision D. No code changed in this pass. Next on your go: implement Layer A `memory_file` + injection first (no migration, one column), then Layer B `memory_search` + `memory_store` tools, with compaction preservation test.

## 10. Revision E: proactivity skill (the agent initiates)

### 10.1 Why a skill and not just prompt text

Prompt text says be proactive. A skill says when, how often, in what shape, and when to stay quiet. Without the second half the agent either spams or never acts. Hermes has no standalone proactivity skill either, which is why your agent feels reactive: cron exists, gateway exists, delegation exists, but nothing tells the model that a finished background job obligates a nudge.

Created in this pass: `backend/src/modules/skills/bundled/proactivity/SKILL.md` (new, on disk). `DEFAULT_BUNDLED_SKILLS` grew 8 to 9. Catalog in `core_context.py` gained a Proactivity section. Verify with `skill_load(name=proactivity)` then `read(path=/workspace/skills/proactivity/SKILL.md)`.

### 10.2 Triggers, grounded in Vigil code you already ship

- `jobs/service.py:25-33 JOB_TYPE_PRIORITIES`: `resume_run 100, execute_run 90, triage_item 60, index_memory 50, poll_connector 20, cleanup_sandbox 10, reindex_memory 5`. Any completion here is a nudge candidate.
- `triage/service.py:73 triage_item`: Jev gate plus fast LLM extractor. A decision above threshold opens a run with summary plus proposed action.
- `memory/service.py:258 index_session` plus idle sweep 900s: a new link between past fact and current session is surfaced once with `memory_search` evidence.
- Stalled run: `awaiting_approval` or `awaiting_background_job` past window. Remind with exact pending question.

### 10.3 Guardrails (what keeps it from becoming spam)

- 3 part nudge only: changed (with evidence run id, path, score), matters (tied to user goal), next step with default plus permission ask when approval policy requires it. Never a plan without offering the first safe step.
- Quiet hours 22:00 to 07:00 local, failures and security break through, rest batches to morning digest. Max 3 unsolicited nudges per day per session. Same nudge twice with no reply backs off with `nudge_suppressed` in MEMORY.md.
- Approval levels unchanged: `read` free, `local` sandboxed, `external` needs decision. Proactivity never bypasses `PartitionPolicy`.
- Verification before announcing: re `read` the artifact, confirm `artifact_ref`, confirm terminal status. One trigger one run, max 2 subagents, record every nudge as one dated MEMORY.md line.

End of revision E. Code changed in this pass: proactivity skill file added, bundled skills 8 to 9, catalog text. Next on your go: wire job completion to auto nudge run + ThinkingCapsule steer box, then delegate cap 2.

## 11. Revision F: proactivity loads at initialisation (done in code)

Implemented in `backend/src/modules/runs/core_context.py`:

- `_load_bundled_skill_body(name)` reads `backend/src/modules/skills/bundled/<name>/SKILL.md` relative to the file, strips YAML frontmatter, returns body or empty string on missing file so prompt builds never fail.
- `build_core_system_prompt(..., skills=None)` now defaults `active` to `DEFAULT_BUNDLED_SKILLS` when skills is None, and when `proactivity` is in the active list it appends `## Proactivity (Always Active)` plus the full skill body before the active skills line. Excluding `proactivity` from the list excludes the body. No `skill_load` call needed.
- Verified live: default prompt is 15739 chars and contains the proactivity body, excluded list omits it, existing `test_bundled_skills_have_valid_manifests_and_core_context_embeds_philosophy` still passes (9 bundled dirs match 9 tuple entries).
- `service.py get_or_create_default_agent_config` docstring corrected to 11 tools and 9 skills with proactivity loaded. Seed path (`service.py:422`) and CLI path (`scripts/try_agent.py:327`) both pass `skills=list(DEFAULT_BUNDLED_SKILLS)`, so both seed and CLI runs start proactive. Stored `system_prompt` rows already seeded before this change keep their old text until deleted; new `vigil-default` rows get the proactivity block.

Note: prompt grows by about 4.4K chars. This is intentional. It is the one skill worth the tokens because it governs every turn. If context gets tight later, trim examples first, never the trigger and guardrail sections.

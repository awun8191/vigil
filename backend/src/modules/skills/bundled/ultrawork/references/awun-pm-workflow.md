# AWUN PM Workflow — Issue Batching + Linear + GitHub Sync

Proven pattern from the AWUN social commerce project management session (May 2026). Used in combination with ultrawork for parallel execution.

## Project Architecture (do not deviate)

```
Frontend Auth: Firebase Auth (email/password + Google)
Backend Auth:  X-API-Key from .env (NO JWT, NO custom auth)
Data Store:    Firestore only (NO SQLite, NO JSON)
Git Branches:  hermes = working branch, main = release
```

**Critical constraint:** Never implement JWT, bcrypt, or custom user authentication. Firebase Auth handles frontend identity; a static API key from `.env` authenticates API calls.

## Workflow

```
1. PICK ~3 issues from Linear board (priority P3 or lower for easy wins, P1-P2 for critical path)
2. DELEGATE in parallel via ultrawork (max 3 sub-agents per wave)
3. REVIEW output — run tests, verify builds, check quality
4. VERIFY changes landed (git status/git diff) — sub-agents work in isolated contexts!
5. FIX simple issues directly (syntax, small bugs, minor omissions)
6. FLAG architectural/infra discoveries as NEW Linear issues (don't let tasks drag)
7. COMMIT each wave as a single commit on the working branch
8. UPDATE Linear — mark issues Done, add comments with what changed
9. MIRROR to GitHub Projects board if needed
10. STOP after the batch — report to user, wait for direction
11. NEVER SPIRAL — don't start a new wave without user approval
```

## Constraint Propagation

When delegating to sub-agents, include hard constraints in EVERY task context:

```
"constraints": ["DO NOT implement custom auth", "DO NOT use JWT", "DO NOT add SQLite"]
```

Sub-agents don't share state — each one needs the full constraint set.

## Sub-agent Workspace Isolation

Sub-agents' file modifications do NOT apply to the parent's working directory. After each wave:

1. Read the task's detailed summary report
2. Run `git status` and `git diff --stat HEAD` to check what changed
3. For any missing changes, re-apply them manually via `patch` using the summary as a guide
4. Then run tests to verify everything works together

## Common Domain Model Patterns (AWUN-specific)

### Cascade Deletes
When a parent entity is deleted, cascade in order:
1. List all child entities via a dedicated `get_all_by_<parent>` method
2. For each child: delete vectors, delete storage images, delete document
3. Delete the parent document
4. Wrap each step in try/except for best-effort cleanup
5. Log warnings for non-fatal errors (e.g., storage unavailable in test env)

### Domain Model Hardening (from_dict)
- Always validate required IDs — raise `ValueError` instead of silently generating UUIDs
- Coerce numeric types (Firestore stores int for whole numbers)
- Add `_require_str` helper for required string fields
- Never accept empty strings for required fields

### Email Uniqueness
- Add `get_by_email` to repository interface + Firestore implementation
- Check in service layer before create AND before update (if email changed)
- Use `where("email", "==", email).limit(1)` for efficient Firestore query

## Merge Strategy for Divergent Branches

When the working branch (hermes) and main have unrelated histories:

```bash
git checkout main
git pull origin main
git merge origin/hermes --allow-unrelated-histories --no-commit
# Resolve conflicts by taking hermes version for all backend/frontend code
git checkout origin/hermes -- <conflicted-files>
# Keep parent's AGENTS.md updates (phase table, Linear table)
# Manually fix AGENTS.md to merge both versions
git rm -f <deleted files from hermes>
git add -A && git commit -m "Merge hermes into main — <summary>"
git push origin main
```

## Verification Gate Per Wave

After each wave completes:
- [ ] Backend tests pass: `cd BACKEND && uv run pytest tests/ -v`
- [ ] Frontend builds: `cd AWUN-DASHBOARD/AwunDashboard && npm run build`
- [ ] No JWT/custom auth introduced
- [ ] No SQLite references introduced
- [ ] No remote operations performed (user pushes themselves)
- [ ] Changes verified via `git diff --stat HEAD` and `git status`
- [ ] Linear issues updated with completion comments
- [ ] AGENTS.md kept current (phase table, Linear issue list, architecture)

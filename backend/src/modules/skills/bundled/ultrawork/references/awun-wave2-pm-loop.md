# AWUN Wave 2 — May 8, 2026

Concrete example of the Issue-Driven PM Loop pattern. Three Phase 1 polish issues delegated in parallel, all completed successfully.

## Issues Selected

| Issue | Priority | Domain | Files Changed | Result |
|-------|----------|--------|---------------|--------|
| AWU-15: Cascade deletes | P2 | Backend | 5 (vendor_service, product_repo abstract + impl, test_vendors) | ✅ 161 tests pass |
| AWU-16: Domain model hardening | P2 | Backend | 2 (vendor.py, product.py) | ✅ 161 tests pass |
| AWU-17: Email/phone validation | P2 | Backend | 6 (vendor_repo abstract + firestore + JSON impl, vendor_service, vendors route) | ✅ 161 tests pass |

Additional: AWU-12 (key rotation, already done by user), AWU-32 (landing page copy, already done)

## Task Contexts

Each task included:
- Specific file paths with line numbers
- Exact code patterns to implement (before/after snippets)
- Available API signatures (e.g., "`vector_repo.delete(product_id) -> None` already exists")
- Import paths for dependent services
- Constraint: "NO JWT, NO custom auth"

## What Worked

- **Ultrawork parallel execution**: All 3 tasks ran simultaneously, completed in ~3 min total wall time
- **Rich context per task**: Each sub-agent had the exact code patterns — no clarifying questions needed
- **Manual re-application pattern**: After delegate_task, parent re-applied changes manually from summaries (sub-agents' file changes don't persist to parent's working directory)
- **Single commit per wave**: All 10 files committed together as `5ba9f86` with descriptive message
- **Linear + GitHub sync**: All issues closed with comments, GitHub #9 closed too

## What Was Painful

### Sub-agent workspace isolation
All 3 sub-agents reported success with detailed summaries, but NONE of their file modifications appeared in the parent's git repo (`git status` showed no changes). The parent had to read each summary, then re-apply every patch manually.

**Mitigation for next wave:** After delegate_task returns, immediately `git diff --stat HEAD` and `git status` to detect this. Then re-apply patch commands using the sub-agent's summary as a guide.

## Key Code Patterns Captured

### Cascade Deletes (vendor_service.py)

```python
async def delete_vendor(self, vendor_id: str) -> bool:
    existing = await self._vendor_repo.get_by_id(vendor_id)
    if not existing:
        return False
    all_products = await self._product_repo.get_all_by_vendor(vendor_id)
    for product in all_products:
        # Best-effort per-step
        try: await self._vector_repo.delete(product.product_id)
        except Exception as e: logger.warning(...)
        try: await self._storage_service.delete_product_images(vendor_id, product.product_id)
        except Exception as e: logger.warning(...)
        try: await self._product_repo.delete(vendor_id, product.product_id)
        except Exception as e: logger.warning(...)
    return await self._vendor_repo.delete(vendor_id)
```

### Domain Model Hardening (vendor.py / product.py)

```python
def _require_str(data: dict[str, Any], key: str) -> str:
    val = data.get(key, "")
    if not val or not isinstance(val, str) or not val.strip():
        raise ValueError(f"Missing required field: {key}")
    return val.strip()

@classmethod
def from_dict(cls, data: dict[str, Any]) -> "Vendor":
    vendor_id = data.get("vendor_id") or data.get("id")
    if not vendor_id:
        raise ValueError("Missing vendor_id in data")
    # ... use _require_str for first_name, last_name, email
```

### Email Uniqueness (vendor_repository + vendor_service)

```python
# Firestore implementation
async def get_by_email(self, email: str) -> Optional[Vendor]:
    query = self._collection.where("email", "==", email).limit(1)
    docs = list(query.stream())
    return Vendor.from_dict(docs[0].to_dict()) if docs else None

# Service layer — create
async def create_vendor(self, vendor: Vendor) -> Vendor:
    existing = await self._vendor_repo.get_by_email(vendor.email)
    if existing:
        raise ValueError(f"Email '{vendor.email}' already exists")
    return await self._vendor_repo.create(vendor)

# Service layer — update (email change)
if "email" in updates and updates["email"].lower() != existing.email.lower():
    existing_by_email = await self._vendor_repo.get_by_email(updates["email"])
    if existing_by_email and existing_by_email.vendor_id != vendor_id:
        raise ValueError(f"Email '{updates['email']}' is already in use")
```

## Verification

After wave completion:
- [x] `uv run pytest tests/ -v` → 161 passed
- [x] All 10 files staged and committed as single commit
- [x] Pushed to main
- [x] Linear issues AWU-12, AWU-15, AWU-16, AWU-17, AWU-32 moved to Done
- [x] GitHub issue #4 (SEC-01), #9 (FE-01) closed
- [x] Comment added to each closed issue with implementation summary

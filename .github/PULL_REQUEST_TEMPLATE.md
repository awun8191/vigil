## What this changes

<!-- One paragraph. State the behavior before and after the change. -->

## Why

<!-- The defect, the missing capability, or the measurement that motivates this change. -->

## Evidence

<!-- Commands that you ran, with the output that matters. A test name, a route count, a number. -->

```text
$ env -u PYTHONPATH uv run pytest -q
```

## Checklist

- [ ] The change follows the module contract: Protocols in `repository.py`, mechanism in `service.py`, a router factory in `router.py`.
- [ ] A new behavior has a test that fails without the change.
- [ ] A new route appears in `openapi.json` with the expected schema.
- [ ] A new migration has a fresh revision number and a downgrade that states its intent.
- [ ] No secret, token, or credential appears in the code, the logs, or the tests.
- [ ] The commits are atomic Conventional Commits.
- [ ] The documentation states the new behavior, when the behavior is visible to a user.

## Related

<!-- Issue numbers, review notes, or a benchmark document. -->

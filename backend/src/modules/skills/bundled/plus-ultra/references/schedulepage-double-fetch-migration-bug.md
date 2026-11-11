# SchedulePage Double-Fetch Bug (July 2026)

## Context

During a project-wide migration from manual `useState`+`useEffect`+`useCallback` data fetching to `@tanstack/react-query v5`, the SchedulePage had a hidden double-fetch bug.

## The Bug

The old code used a `loadSchedule` useCallback that was called by a `useEffect` on mount:

```
loadSchedule useCallback → calls api.schedule() → sets schedule state
useEffect → calls loadSchedule() on mount
```

The migration replaced this with `useQuery`:

```
useQuery({ queryKey: ['schedule'], queryFn: api.schedule, enabled: shouldFetchSchedule })
```

But the old `loadSchedule` useCallback was minimally patched instead of removed. It was changed to just call `queryClient.invalidateQueries(['schedule'])` — but the `useEffect` that called it on mount was left in place.

**Result**: On every page mount, `useQuery` fired one request (fresh), then the dead useEffect fired `invalidateQueries` (marks stale), causing React Query to fire a second request (background refetch). Two back-to-back API calls on every page load.

## Root Cause

The `loadSchedule` callback was still referenced by:
1. Its own `useCallback` declaration
2. The `useEffect` that called it
3. No other callers (all mutation handlers had been updated to use `queryClient.invalidateQueries` directly)

TypeScript could not catch this — the code compiled cleanly. The double-fetch was only visible in browser DevTools Network tab.

## Fix

Remove the `loadSchedule` useCallback and its `useEffect` entirely. The `useQuery` with `enabled` flag handles:
- Initial data fetch (when user is signed in)
- No fetch when user is signing in (enabled=false while loading)
- No fetch when signed out (enabled=false)

## Prevention

When migrating from manual fetch to React Query:
1. After swapping in `useQuery`, search for the old fetch function name (e.g. `loadSchedule`, `loadCourses`).
2. Check every reference — is it still needed? Mutation callers should use `invalidateQueries`. The initial load is handled by `useQuery`'s `enabled` flag.
3. Remove the old callback + useEffect pair entirely, even if the callback was minimally patched.
4. Verify in browser DevTools Network tab that only 1 request fires on mount.

## Type Mismatch

UseQuery returns `T | undefined`, but existing code often expects `T | null`. Fix with `?? null` at the call site. TypeScript strict null checks would catch this, but many projects don't enable them.

# Monolith Refactoring with Ultrawork — Session-Proven Pattern

Proven in session 20260504: decomposed a 2,364-line monolithic React App.tsx into 5 hooks + 5 pages + 1 context provider + 1 slim shell across ~5 parallel waves in under 30 minutes wall clock.

## The Pattern

Large single-file React components (2k+ lines) follow a natural decomposition:

```
Monolith (App.tsx) 
  → Hooks (data/logic extraction)
  → Page Components (UI extraction per tab/section)  
  → Context Provider (unified state wiring)
  → Slim App Shell (entry point, layout, routing)
```

Each layer is a separate parallel wave because **hooks must exist before pages** and **pages must exist before the App shell**.

## Wave Planning

### Wave 1: Hooks (3 tasks parallel)
Extract logical state/behavior clusters from the monolith. For a typical React single-page app with tabs:

| Hook | What It Extracts | Lines in Monolith |
|------|-----------------|-------------------|
| `useAuth` | Auth state, user profile, login/logout, theme/sidebar prefs | 358-513 |
| `useCart` | Cart state, add/remove/checkout, delivery windows, balance validation | 608-713, 2218-2357 |
| `useOrders` | Firestore listeners for orders/budget/restaurants/staff, mock data | 365-367, 475-594 |

### Wave 2: Remaining Hooks + First Page (3 tasks parallel)

| Hook/Page | What It Extracts |
|-----------|-----------------|
| `useWallet` | Wallet funding flow (method, amount, processing state) |
| `useOrg` | Org registration, staff invite, join-by-code, batch toggle |
| `HomePage` | First tab's JSX (can use mock data types since hooks aren't wired yet) |

### Wave 3: Remaining Pages (3 tasks parallel)

| Page | Tab Content |
|------|------------|
| `WalletPage` | Wallet tab JSX |
| `BudgetPage` | Budget/Plan Genius tab JSX |
| `OrgPage` | Organization tab JSX |

### Wave 4: Context + Remaining Components (2-3 tasks)
- `AppContext.tsx` — Combines all 5 hooks, provides combined type
- `ProfilePage` — Last tab JSX
- `CartDrawer` / `ErrorBoundary` — Extracted sub-components

### Wave 5: App Shell Rewrite
- Rewrite `App.tsx` as a slim shell: `ErrorBoundary > AppProvider > AppContent > Layout + Pages`
- 600-800 lines instead of 2,300+

## Context Crafting for Code Extraction

The secret to fast, accurate subagent output: include **everything they need to avoid asking questions**.

### Bad context:
```
Extract the auth logic into a hook.
```

### Good context:
```
File: /path/to/App.tsx
The auth logic spans lines 358-513 in the current App.tsx.
Key state: user (FirebaseUser|null), profile (UserProfile|null), loading (boolean).
Flow: onAuthStateChanged → creates profile if new user → sets up onSnapshot listeners.
Dev mode: in DEV mode, mock a dev user and profile directly.
The hook should return: { user, profile, loading, isDarkMode, setIsDarkMode, 
  isSidebarCollapsed, setIsSidebarCollapsed, handleLogin, handleLogout }
Use these types from types.ts: UserProfile (uid, email, displayName, role, walletBalance, ...)
Firebase setup is in src/lib/firebase.ts (exported auth, db).
Theme toggle writes to Firestore via setDoc(doc(db, 'users', user.uid), 
  { theme: newTheme ? 'dark' : 'light' }, { merge: true }).
Write to /home/user/project/src/hooks/useAuth.ts
```

### Context components:
1. **File path + line ranges** — Where the source code lives
2. **Key state + types** — Exact variable names, interfaces, and shapes
3. **Data flow** — How things connect (listeners, effects, callbacks)
4. **Edge cases** — Dev mode, loading states, error handling
5. **Output file path** — Exactly where to write
6. **Import paths** — Relative import path conventions the project uses
7. **Return type** — What the hook/component should expose

## Handling Subagent Timeouts

Subagents can timeout (600s default) when the task is too large or context is insufficient. Mitigations:

- **Keep each task focused**: One hook per task, not a "create all hooks" mega-task
- **Limit file reads**: Tell the subagent exactly which files to read; don't let it discover the codebase
- **Use line ranges**: `read_file(path, offset=N, limit=M)` to feed only relevant sections
- **If timeout happens**: The partial output is lost. Re-task with even tighter scope

## Post-Wave Integration

After parallel waves, expect **type mismatch errors** because subagents independently create interfaces. Common issues:

1. **Prop interface mismatches** — Page expects prop X but context provides Y. Fix: read both files, unify the interface.
2. **Missing exports** — Context value missing a setter that pages need. Fix: add to interface + value object.
3. **Import path errors** — Subagent writes relative import wrong. Fix: verify imports against project structure.
4. **Type name collisions** — Subagent re-exports types that already exist. Fix: deduplicate.

### Integration workflow:
```
1. npm run build  → collect ALL errors
2. Group errors by file (not by error type)
3. Fix each file's errors in batch (one patch per file, not per error)
4. Rebuild → verify clean
```

## Verification Checklist

After all waves complete:

- [ ] `tsc --noEmit` passes (0 errors)
- [ ] `npm run build` succeeds
- [ ] Dev server starts without network dependencies
- [ ] All tabs render without crash
- [ ] Mock data populates all views
- [ ] Theme toggle works (light/dark)
- [ ] Sidebar navigation works

# A2UI Panel System & Design-Consistency Guardrails — Design Specification

**Date:** 2026-11-19  
**Status:** Draft for User Review  
**Scope:** Replace Vigil's legacy 4-template dashboard widgets (`metric_card`, `data_table`, `task_list`, `status_feed`) with a persistent, periodically self-updating **A2UI (Agent-to-User Interface v0.9/v1.0)** panel architecture, plus a host-token injection bridge for pinned HTML micro-apps.

---

## 1. Goals & Non-Goals

### Goals
1. **A2UI-Only Persistent Dashboard Panels (`widget_type = "a2ui_surface"`):** Remove the four non-A2UI widget types (`metric_card`, `data_table`, `task_list`, `status_feed`) and their legacy rows/components so every dashboard panel is an A2UI surface governed by the catalog `urn:vigil:a2ui:catalog:panels:v1`.
2. **Strict Structure vs. State Separation (`components` vs. `data_model`):** Follow A2UI v0.9/v1.0 by storing a flat component adjacency list (`components` + `root`) bound to an isolated JSON `data_model` via RFC 6901 JSON Pointers (`{ "path": "/..." }`).
3. **Drift-Free Periodic Background Refreshes (`updateDataModel`):** Ensure scheduled (`15 | 60 | 240 | 1440` min) and manual refreshes update **only `data_model`** (or validate a full surface payload), leaving `components` frozen so background refreshes can never mutate layout or break visual consistency.
4. **Structural & Visual Design-Consistency Enforcement:**
   - **Token-Only Styling:** Component props accept only semantic design-token enums (`tone`, `emphasis`, `gap`, `align`, `icon`, `size`) mapped to Vigil's CSS variables (`--text`, `--text-muted`, `--accent`, `--border`, `--surface-solid`, `--r-inner`). No raw hex codes, pixel strings, or inline CSS styles are accepted.
   - **Server-Side Grammar Validator:** Enforce DAG acyclicity, root/child reachability, tree depth $\le 3$, node budget ($\le 12$ for `sm`/`md`, $\le 16$ for `lg`), anti-squish layout rules (no `DataTable` inside multi-child `Row`), and RFC 6901 pointer resolution.
   - **Host-Owned `PanelShell`:** Outer squircle border, concentric padding, header icon, title, freshness badge, `"Ask Vigil"` conversational drill-down action, and remove button are owned strictly by the host shell.
5. **Token-Bridged Micro-App Escape Hatch:** Inject Vigil's active light/dark CSS custom properties and base typography rules into `Pinned Micro-Apps` (`srcDoc` iframes) in `WidgetsRail.tsx` so bespoke interactive artifacts match Obsidian Horizon and Tactile Alabaster themes.

### Non-Goals
- Pulling in external widget dependencies such as Vercel `@json-render/*` or OpenAI `ChatKit`.
- Replacing `HarkLeftRail.tsx`'s four system briefing views (`today | usage | agenda | waiting`) in this pass.

---

## 2. Database & Migration Design

### 2.1 Alembic Migration (`backend/alembic/versions/0014_a2ui_widgets.py`)
- **Revision:** `"0014"`, `down_revision = "0013"`.
- **Upgrade steps:**
  1. Delete all legacy non-A2UI rows:
     ```sql
     DELETE FROM widgets WHERE widget_type != 'a2ui_surface';
     ```
  2. Drop the old `ck_widgets_widget_type` check constraint and create the updated constraint:
     ```sql
     ALTER TABLE widgets DROP CONSTRAINT ck_widgets_widget_type;
     ALTER TABLE widgets ADD CONSTRAINT ck_widgets_widget_type CHECK (widget_type in ('a2ui_surface'));
     ```
- **Model Definition (`backend/src/schemas/widgets.py`):**
  - Update `WidgetModel.__table_args__` so `ck_widgets_widget_type` enforces `"widget_type in ('a2ui_surface')"`.

---

## 3. Backend A2UI Wire Schema & Grammar Validator (`backend/src/modules/widgets/data.py`)

### 3.1 Semantic Design-Token Enums
```python
WidgetType = Literal["a2ui_surface"]
A2UICatalogId = Literal["urn:vigil:a2ui:catalog:panels:v1"]
A2UISize = Literal["sm", "md", "lg"]
A2UITone = Literal["neutral", "accent", "positive", "warning", "danger"]
A2UIEmphasis = Literal["hero", "primary", "secondary", "muted", "mono"]
A2UIGap = Literal["tight", "normal", "relaxed"]
A2UIAlign = Literal["start", "center", "end", "between"]
A2UIIcon = Literal[
    "activity", "calendar", "check-square", "clock", "credit-card",
    "flame", "git-branch", "globe", "heart-pulse", "inbox",
    "layers", "mail", "music", "package", "sparkles",
    "train-front", "trending-up", "zap",
]
```

### 3.2 A2UI Data Binding Primitive (`A2UIBinding`)
In A2UI v0.9/v1.0, dynamic component properties are either a native JSON literal or a JSON Pointer reference:
```python
class A2UIPointer(BaseModel):
    """RFC 6901 JSON Pointer reference into the surface data_model."""
    model_config = ConfigDict(extra="forbid")
    path: str = Field(..., min_length=2, max_length=128, pattern=r"^/[^~]*(~[01][^~]*)*$")
```

### 3.3 Catalog Component Nodes (`urn:vigil:a2ui:catalog:panels:v1`)
Each entry in `components` is a discriminated or validated `A2UIComponentNode`:
```python
class A2UIComponentNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    component: Literal[
        "Column", "Row", "Divider",
        "Metric", "Sparkline", "ProgressBar",
        "Badge", "KeyValueList", "DataTable",
        "Text", "CheckList", "Button",
    ]
    props: dict[str, Any] = Field(default_factory=dict)
    children: list[str] = Field(default_factory=list, max_length=8)
```

#### Component Prop Contracts (Validated per `component` type with `extra="forbid"`)
| Component | Allowed `children` | Validated `props` Schema |
| :--- | :--- | :--- |
| `Column` | `1..8` IDs | `gap: A2UIGap = "normal"` |
| `Row` | `1..4` IDs | `gap: A2UIGap = "normal"`, `align: A2UIAlign = "center"`, `wrap: bool = False` |
| `Divider` | `0` | `{}` |
| `Metric` | `0` | `label: str \| A2UIPointer`, `value: str \| A2UIPointer`, `delta?: str \| A2UIPointer`, `caption?: str \| A2UIPointer`, `tone: A2UITone = "neutral"` |
| `Sparkline` | `0` | `points: list[float] \| A2UIPointer` (4..30 numbers when literal), `label?: str \| A2UIPointer`, `tone: A2UITone = "accent"` |
| `ProgressBar` | `0` | `label?: str \| A2UIPointer`, `value: float \| A2UIPointer`, `max: float = 100.0` ($> 0$), `tone: A2UITone = "accent"`, `show_value: bool = True` |
| `Badge` | `0` | `text: str \| A2UIPointer`, `tone: A2UITone = "neutral"` |
| `KeyValueList` | `0` | `items: list[KeyValueItem] \| A2UIPointer` (max 15), `max_items: int = Field(default=5, ge=1, le=15)` where `KeyValueItem = { label: str, value: str, meta?: str, tone?: A2UITone }` |
| `DataTable` | `0` | `columns: list[str]` (`1..6`, each `1..48` chars), `rows: list[list[str]] \| A2UIPointer` (max 25 rows) |
| `Text` | `0` | `text: str \| A2UIPointer`, `emphasis: A2UIEmphasis = "primary"` |
| `CheckList` | `0` | `items: list[TaskItem] \| A2UIPointer` (max 25) where `TaskItem = { id: str, label: str, done: bool = False }` |
| `Button` | `0` | `label: str \| A2UIPointer`, `variant: Literal["primary", "secondary", "ghost"] = "secondary"`, `action: A2UIAction` |

Where `A2UIAction` supports A2UI v0.9 client function calls and server/agent events:
```python
class A2UIFunctionCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call: Literal["ask_vigil", "refresh_panel"]
    prompt: str | None = Field(default=None, max_length=280)

class A2UIEventAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=64)
    context: dict[str, Any] = Field(default_factory=dict)

class A2UIAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    functionCall: A2UIFunctionCall | None = None
    event: A2UIEventAction | None = None
```
*(Exactly one of `functionCall` or `event` must be set.)*

### 3.4 Surface Envelope & Grammar Rules (`A2UISurfacePayload`)
```python
class A2UISurfacePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["v0.9"] = "v0.9"
    catalog_id: A2UICatalogId = "urn:vigil:a2ui:catalog:panels:v1"
    size: A2UISize = "md"
    icon: A2UIIcon | None = None
    root: str = Field(..., min_length=1, max_length=64)
    components: list[A2UIComponentNode] = Field(..., min_length=1, max_length=16)
    data_model: dict[str, Any] = Field(default_factory=dict)
```

**Deterministic Grammar Validator (`@model_validator(mode="after")`):**
1. **Unique Component IDs & Root Existence:** Every `node.id` in `components` must be unique, and `root` must exist in `components`.
2. **Container vs. Leaf Discipline:** Only `Column` and `Row` may have non-empty `children`. Every other component must have `children == []`.
3. **Acyclic Connected DAG & Depth Cap ($\le 3$):** DFS from `root` verifies all referenced child IDs exist, detects any cycle (`ValueError("A2UI component graph contains a cycle")`), verifies no orphan nodes exist outside the `root` subtree, and enforces maximum tree depth $\le 3$ (where `root` is depth 1).
4. **Size & Glanceability Budget:**
   - `size in ("sm", "md")`: at most **12** components total.
   - `size == "lg"`: at most **16** components total.
   - `size == "sm"`: at most **1** heavy visual (`Sparkline` or `DataTable`).
   - **Anti-Squish Rule:** A `Row` with $> 1$ children cannot contain a `DataTable` child.
5. **RFC 6901 JSON Pointer Verification:** Every `A2UIPointer(path=...)` used in any component prop must resolve against `data_model` via `resolve_json_pointer(data_model, pointer.path)` and match the expected target shape (e.g., `Sparkline.points` resolves to a list of $4..30$ numbers; `KeyValueList.items` resolves to a list of `KeyValueItem` dicts; `DataTable.rows` resolves to a list of string lists; `CheckList.items` resolves to a list of `TaskItem` dicts).

### 3.5 Partial `updateDataModel` Support (`merge_a2ui_payload_update`)
When `update_widget` or `handle_refresh_widget` updates an existing `a2ui_surface` widget, the caller may supply:
1. A **full `A2UISurfacePayload`** (A2UI `updateComponents` + `updateDataModel`), OR
2. A **data-only update** (`{ "data_model": { ... } }` or a raw `data_model` dict when triggered by `WIDGET_CONNECTOR_QUERY` in `worker.py`).
`merge_a2ui_payload_update(existing_payload, incoming_payload)` merges the new `data_model` into the existing frozen layout (`version`, `catalog_id`, `size`, `icon`, `root`, `components`), truncates list lengths in `data_model` to schema caps (`max 25` table rows / checklist items, `max 15` key-value items, `max 30` sparkline points), and re-validates the resulting `A2UISurfacePayload` so every JSON Pointer binding remains valid.

---

## 4. Agent Tools & Self-Knowledge (`backend/src/modules/runs/tools/widgets.py`)

- **`create_widget`:**
  - `widget_type: WidgetType = "a2ui_surface"` (defaults to `"a2ui_surface"` so the agent does not even need to passlegacy type names).
  - Validates `data_payload` via `validate_widget_payload("a2ui_surface", args.data_payload)`.
- **`update_widget`:**
  - Accepts `widget_id: UUID` and `data_payload: dict[str, Any]` (which can be either `{ "data_model": ... }` for fast A2UI `updateDataModel` state updates or a full `A2UISurfacePayload` when redesigning components).
- **`vigil-agent/SKILL.md`:**
  - Update `backend/src/modules/skills/bundled/vigil-agent/SKILL.md` to document the A2UI panel system (`widget_type="a2ui_surface"`, catalog `urn:vigil:a2ui:catalog:panels:v1`, the 12 components, JSON Pointer `{ "path": "/..." }` bindings, and Hark Pro's clarification-first workflow).

---

## 5. Frontend A2UI Renderer & Token-Bridged Micro-Apps (`web/src/components/widgets/`)

### 5.1 Remove Legacy Non-A2UI Widget Components
- Delete `web/src/components/widgets/MetricCard.tsx`, `DataTable.tsx`, `TaskList.tsx`, and `StatusFeed.tsx`.
- Update `web/src/types.ts` so `WidgetType = 'a2ui_surface'`.

### 5.2 A2UI Client Renderer (`web/src/components/widgets/A2UISurface.tsx` & `registry.tsx`)
- **`resolveJsonPointer(dataModel, path)`:** Evaluates RFC 6901 paths (`/a/b/0`, `~0` $\to$ `~`, `~1` $\to$ `/`). Returns `undefined` safely if a path is missing.
- **Zod Surface Schema (`a2uiSurfacePayloadSchema`):** Mirrors `A2UISurfacePayload` in `registry.tsx`. If `safeParse` fails or `root` is missing, renders `FallbackCard`.
- **Host `PanelShell` Header & Conversational Drill-Down:**
  - Renders the surface's allowlisted Lucide `icon` (mapped from `A2UIIcon`), `title`, and a `"Ask Vigil"` button (`MessageSquarePlus`) next to the `Remove` button in `WidgetsRail.tsx` / `WidgetView`.
  - Clicking `"Ask Vigil"` or an A2UI `Button` with `functionCall: { call: "ask_vigil", prompt? }` navigates to `/chat` (or dispatches a custom `vigil:prefill-chat` event / query param `?prompt=...`) pre-filled with a concise panel reference prompt.
- **Visual Rendering of the 12 Primitives:**
  - All colors resolve through a semantic tone map:
    - `neutral` $\to$ `var(--text)` / `var(--text-muted)`
    - `accent` $\to$ `var(--accent)` / `var(--accent-soft)`
    - `positive` $\to$ emerald-tinted `color-mix` harmonizing with Obsidian/Alabaster
    - `warning` $\to$ amber-tinted `color-mix`
    - `danger` $\to$ rose-tinted `color-mix`
  - `Sparkline` renders an inline responsive SVG polyline + gradient fill using the semantic tone stroke.
  - `ProgressBar` renders a concentric rounded track (`rounded-full`) with smooth width transition and optional percentage readout.

### 5.3 Token-Bridged Micro-App Sandbox (`web/src/components/widgets/microAppTheme.ts` & `WidgetsRail.tsx`)
- Add `buildThemedMicroAppSrcDoc(rawHtml: string): string` used when rendering `Pinned Micro-Apps` iframes in `WidgetsRail.tsx`:
  - Strips external Google Fonts `<link>` stylesheet tags.
  - Injects a `<style id="vigil-host-tokens">` block copying the document's active computed CSS custom properties (`--bg`, `--surface`, `--surface-solid`, `--text`, `--text-muted`, `--border`, `--accent`, `--accent-soft`, `--font-sans`, `--r-inner`, plus aliases `--foreground`, `--muted-foreground`, `--card`, `--card-border`, `--primary`) and setting transparent body defaults (`background: transparent; color: var(--text); font-family: var(--font-sans); margin: 0; overflow-x: hidden;`).

---

## 6. Testing & Verification Plan

1. **Backend Unit Tests (`backend/tests/unit/test_widgets.py`):**
   - Replace legacy 4-type fixtures with A2UI surface fixtures.
   - Test valid A2UI compositions (`Metric` + `Sparkline` + `KeyValueList`, `DataTable`, `CheckList`, `ProgressBar`, `Button`).
   - Test grammar rejections: cycle detection, orphan node rejection, depth $> 3$ rejection, leaf with `children` rejection, `DataTable` inside multi-child `Row` rejection, `size="sm"` with 2 heavy visuals rejection, unresolved JSON Pointer rejection, unknown prop/token rejection (`extra="forbid"`).
   - Test `updateDataModel`-only refresh in `handle_refresh_widget` and `update_widget` (verifying `components` remain untouched while `data_model` and `version` update).
   - Test `test_skill_manifest.py`, `test_hermes_memory.py`, and `test_tools.py` pass with the updated `vigil-agent/SKILL.md`.
2. **Frontend Tests & Build (`web/`):**
   - Update `web/src/components/widgets/__tests__/` (or widget unit tests) for A2UI surfaces, RFC 6901 pointer resolution, `FallbackCard` fallback on bad schema, and `buildThemedMicroAppSrcDoc`.
   - Verify `cd web && npm run build` passes with zero TypeScript errors.

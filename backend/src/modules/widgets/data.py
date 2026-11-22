"""Pydantic v2 A2UI (v0.9) wire schemas and design-consistency grammar validator for widgets."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

A2UI_PANEL_CATALOG_ID = "urn:vigil:a2ui:catalog:panels:v1"

WidgetType = Literal["a2ui_surface"]
A2UICatalogId = Literal["urn:vigil:a2ui:catalog:panels:v1"]
A2UISize = Literal["sm", "md", "lg"]
A2UITone = Literal["neutral", "accent", "positive", "warning", "danger"]
A2UIEmphasis = Literal["hero", "primary", "secondary", "muted", "mono"]
A2UIGap = Literal["tight", "normal", "relaxed"]
A2UIAlign = Literal["start", "center", "end", "between"]
A2UIIcon = Literal[
    "activity",
    "calendar",
    "check-square",
    "clock",
    "credit-card",
    "flame",
    "git-branch",
    "globe",
    "heart-pulse",
    "inbox",
    "layers",
    "mail",
    "music",
    "package",
    "sparkles",
    "train-front",
    "trending-up",
    "zap",
]
A2UIComponentType = Literal[
    "Column",
    "Row",
    "Divider",
    "Metric",
    "Sparkline",
    "ProgressBar",
    "Badge",
    "KeyValueList",
    "DataTable",
    "Text",
    "CheckList",
    "Button",
    "TextField",
    "ChoiceGroup",
]

#: Maximum number of rows allowed in an A2UI DataTable.
A2UI_TABLE_MAX_ROWS = 25
#: Maximum number of items allowed in an A2UI CheckList.
A2UI_CHECKLIST_MAX_ITEMS = 25
#: Maximum number of items allowed in an A2UI KeyValueList.
A2UI_KV_MAX_ITEMS = 15
#: Maximum number of points allowed in an A2UI Sparkline.
A2UI_SPARKLINE_MAX_POINTS = 30
#: Minimum number of points required in an A2UI Sparkline.
A2UI_SPARKLINE_MIN_POINTS = 4
#: Maximum tree depth for an A2UI surface (root is depth 1).
A2UI_MAX_TREE_DEPTH = 3


class A2UIPointer(BaseModel):
    """RFC 6901 JSON Pointer reference into the surface data_model."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        ...,
        min_length=2,
        max_length=128,
        pattern=r"^/[^~]*(~[01][^~]*)*$",
    )


def resolve_json_pointer(data_model: dict[str, Any], path: str) -> Any:
    """Resolve an RFC 6901 JSON Pointer against ``data_model``.

    Raises:
        ValueError: The pointer syntax is invalid or the referenced key/index does not exist.
    """
    if path == "/":
        return data_model
    if not path.startswith("/"):
        raise ValueError(f"JSON Pointer must start with '/': {path!r}")
    segments = path.split("/")[1:]
    current: Any = data_model
    for raw_segment in segments:
        segment = raw_segment.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if segment not in current:
                raise ValueError(f"JSON Pointer {path!r} could not resolve key {segment!r}")
            current = current[segment]
        elif isinstance(current, list):
            if not segment.isdigit():
                raise ValueError(
                    f"JSON Pointer {path!r} expected list index, got {segment!r}"
                )
            idx = int(segment)
            if idx < 0 or idx >= len(current):
                raise ValueError(f"JSON Pointer {path!r} list index {idx} out of range")
            current = current[idx]
        else:
            raise ValueError(
                f"JSON Pointer {path!r} cannot traverse scalar at {segment!r}"
            )
    return current


def set_json_pointer(data_model: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """Return a copy of ``data_model`` with ``value`` set at RFC 6901 ``path``."""
    if path == "/":
        if not isinstance(value, dict):
            raise ValueError("Root JSON Pointer '/' update requires a JSON object value")
        return deepcopy(value)
    if not path.startswith("/"):
        raise ValueError(f"JSON Pointer must start with '/': {path!r}")
    updated = deepcopy(data_model)
    segments = [seg.replace("~1", "/").replace("~0", "~") for seg in path.split("/")[1:]]
    current: Any = updated
    for seg in segments[:-1]:
        if isinstance(current, dict):
            if seg not in current or not isinstance(current[seg], (dict, list)):
                current[seg] = {}
            current = current[seg]
        else:
            raise ValueError(f"Cannot traverse non-object at segment {seg!r} for {path!r}")
    last = segments[-1]
    if not isinstance(current, dict):
        raise ValueError(f"Cannot assign key {last!r} on non-object for {path!r}")
    current[last] = deepcopy(value)
    return updated


class KeyValueItem(BaseModel):
    """One labeled row inside an A2UI KeyValueList component."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(..., min_length=1, max_length=120)
    value: str = Field(..., min_length=1, max_length=120)
    meta: str | None = Field(default=None, max_length=64)
    tone: A2UITone = "neutral"


class TaskItem(BaseModel):
    """One checkable row inside an A2UI CheckList component."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="", max_length=64)
    label: str = Field(..., min_length=1, max_length=200)
    done: bool = False

    @model_validator(mode="after")
    def _ensure_id(self) -> TaskItem:
        if not self.id.strip():
            slug = "".join(ch if ch.isalnum() else "_" for ch in self.label.lower())[:32].strip("_")
            self.id = f"task_{slug or 'item'}"
        return self


class A2UIFunctionCall(BaseModel):
    """Client-handled function call inside an A2UI Button action."""

    model_config = ConfigDict(extra="forbid")

    call: Literal["ask_vigil", "refresh_panel"]
    prompt: str | None = Field(default=None, max_length=280)


class A2UIEventAction(BaseModel):
    """Agent/server event dispatched from an A2UI Button action."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=64)
    context: dict[str, Any] = Field(default_factory=dict)


class A2UIAction(BaseModel):
    """A2UI v0.9 action envelope supporting either functionCall or event."""

    model_config = ConfigDict(extra="forbid")

    functionCall: A2UIFunctionCall | None = None
    event: A2UIEventAction | None = None

    @model_validator(mode="after")
    def _check_exactly_one_target(self) -> A2UIAction:
        has_fn = self.functionCall is not None
        has_ev = self.event is not None
        if has_fn == has_ev:
            raise ValueError("A2UIAction requires exactly one of 'functionCall' or 'event'")
        return self


class ColumnProps(BaseModel):
    """Props for an A2UI Column layout container."""

    model_config = ConfigDict(extra="forbid")

    gap: A2UIGap = "normal"


class RowProps(BaseModel):
    """Props for an A2UI Row layout container."""

    model_config = ConfigDict(extra="forbid")

    gap: A2UIGap = "normal"
    align: A2UIAlign = "center"
    wrap: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize_justify_alias(cls, data: Any) -> Any:
        if isinstance(data, dict) and "justify" in data:
            normalized = dict(data)
            justify_val = normalized.pop("justify")
            if "align" not in normalized and justify_val in ("start", "center", "end", "between"):
                normalized["align"] = justify_val
            return normalized
        return data


class DividerProps(BaseModel):
    """Props for an A2UI Divider component."""

    model_config = ConfigDict(extra="forbid")


class MetricProps(BaseModel):
    """Props for an A2UI Metric display component."""

    model_config = ConfigDict(extra="forbid")

    label: Annotated[str, Field(min_length=1, max_length=80)] | A2UIPointer = "Metric"
    value: Annotated[str, Field(min_length=1, max_length=64)] | A2UIPointer
    delta: Annotated[str, Field(max_length=64)] | A2UIPointer | None = None
    caption: Annotated[str, Field(max_length=200)] | A2UIPointer | None = None
    tone: A2UITone = "neutral"


class SparklineProps(BaseModel):
    """Props for an A2UI Sparkline trend component."""

    model_config = ConfigDict(extra="forbid")

    points: (
        Annotated[
            list[float],
            Field(
                min_length=A2UI_SPARKLINE_MIN_POINTS,
                max_length=A2UI_SPARKLINE_MAX_POINTS,
            ),
        ]
        | A2UIPointer
    )
    label: Annotated[str, Field(max_length=80)] | A2UIPointer | None = None
    tone: A2UITone = "accent"


class ProgressBarProps(BaseModel):
    """Props for an A2UI ProgressBar goal tracker component."""

    model_config = ConfigDict(extra="forbid")

    label: Annotated[str, Field(max_length=80)] | A2UIPointer | None = None
    value: float | A2UIPointer
    max: float = Field(default=100.0, gt=0)
    tone: A2UITone = "accent"
    show_value: bool = True


class BadgeProps(BaseModel):
    """Props for an A2UI Badge status pill component."""

    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, Field(min_length=1, max_length=64)] | A2UIPointer
    tone: A2UITone = "neutral"


class KeyValueListProps(BaseModel):
    """Props for an A2UI KeyValueList component."""

    model_config = ConfigDict(extra="forbid")

    items: (
        Annotated[list[KeyValueItem], Field(max_length=A2UI_KV_MAX_ITEMS)]
        | A2UIPointer
    )
    max_items: int = Field(default=5, ge=1, le=A2UI_KV_MAX_ITEMS)


class DataTableProps(BaseModel):
    """Props for an A2UI DataTable component."""

    model_config = ConfigDict(extra="forbid")

    columns: list[Annotated[str, Field(min_length=1, max_length=48)]] = Field(
        ..., min_length=1, max_length=6
    )
    rows: (
        Annotated[
            list[list[Annotated[str, Field(max_length=200)]]],
            Field(max_length=A2UI_TABLE_MAX_ROWS),
        ]
        | A2UIPointer
    ) = Field(default_factory=list)


class TextProps(BaseModel):
    """Props for an A2UI Text block component."""

    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, Field(min_length=1, max_length=280)] | A2UIPointer
    emphasis: A2UIEmphasis = "primary"

    @model_validator(mode="before")
    @classmethod
    def _normalize_text_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        weight = normalized.pop("weight", None)
        variant = normalized.pop("variant", None)
        if "emphasis" not in normalized:
            if weight in ("bold", "semibold") or variant in ("h1", "h2", "h3", "hero", "title", "heading"):
                normalized["emphasis"] = "hero"
            elif variant in ("muted", "caption", "subtle"):
                normalized["emphasis"] = "muted"
            elif variant in ("secondary", "subtitle"):
                normalized["emphasis"] = "secondary"
            elif variant in ("mono", "code"):
                normalized["emphasis"] = "mono"
        return normalized


class CheckListProps(BaseModel):
    """Props for an A2UI CheckList component."""

    model_config = ConfigDict(extra="forbid")

    items: (
        Annotated[list[TaskItem], Field(max_length=A2UI_CHECKLIST_MAX_ITEMS)]
        | A2UIPointer
    )


class ButtonProps(BaseModel):
    """Props for an A2UI Button component."""

    model_config = ConfigDict(extra="forbid")

    label: Annotated[str, Field(min_length=1, max_length=48)] | A2UIPointer
    variant: Literal["primary", "secondary", "ghost"] = "secondary"
    action: A2UIAction


class TextFieldProps(BaseModel):
    """Props for an A2UI TextField input component."""

    model_config = ConfigDict(extra="forbid")

    label: Annotated[str, Field(max_length=120)] | A2UIPointer | None = None
    placeholder: Annotated[str, Field(max_length=160)] | None = None
    value: Annotated[str, Field(max_length=500)] | A2UIPointer = ""


class ChoiceOptionItem(BaseModel):
    """One selectable option inside an A2UI ChoiceGroup component."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)


class ChoiceGroupProps(BaseModel):
    """Props for an A2UI ChoiceGroup single/multi-select input component."""

    model_config = ConfigDict(extra="forbid")

    label: Annotated[str, Field(max_length=200)] | A2UIPointer | None = None
    options: Annotated[list[ChoiceOptionItem], Field(min_length=1, max_length=10)]
    multi_select: bool = False
    value: str | list[str] | A2UIPointer = ""


COMPONENT_PROP_MODELS: dict[str, type[BaseModel]] = {
    "Column": ColumnProps,
    "Row": RowProps,
    "Divider": DividerProps,
    "Metric": MetricProps,
    "Sparkline": SparklineProps,
    "ProgressBar": ProgressBarProps,
    "Badge": BadgeProps,
    "KeyValueList": KeyValueListProps,
    "DataTable": DataTableProps,
    "Text": TextProps,
    "CheckList": CheckListProps,
    "Button": ButtonProps,
    "TextField": TextFieldProps,
    "ChoiceGroup": ChoiceGroupProps,
}

_CONTAINER_COMPONENTS = frozenset({"Column", "Row"})
_HEAVY_VISUAL_COMPONENTS = frozenset({"Sparkline", "DataTable"})

_SPARKLINE_POINTS_ADAPTER = TypeAdapter(
    Annotated[
        list[float],
        Field(
            min_length=A2UI_SPARKLINE_MIN_POINTS,
            max_length=A2UI_SPARKLINE_MAX_POINTS,
        ),
    ]
)
_KV_ITEMS_ADAPTER = TypeAdapter(
    Annotated[list[KeyValueItem], Field(max_length=A2UI_KV_MAX_ITEMS)]
)
_TABLE_ROWS_ADAPTER = TypeAdapter(
    Annotated[
        list[list[Annotated[str, Field(max_length=200)]]],
        Field(max_length=A2UI_TABLE_MAX_ROWS),
    ]
)
_CHECKLIST_ITEMS_ADAPTER = TypeAdapter(
    Annotated[list[TaskItem], Field(max_length=A2UI_CHECKLIST_MAX_ITEMS)]
)


def _validate_resolved_pointer_value(
    component: str,
    prop_name: str,
    resolved: Any,
) -> None:
    """Validate that a pointer-resolved value from ``data_model`` fits its prop contract."""
    if component == "Sparkline" and prop_name == "points":
        _SPARKLINE_POINTS_ADAPTER.validate_python(resolved)
        return
    if component == "ProgressBar" and prop_name == "value":
        if isinstance(resolved, bool) or not isinstance(resolved, (int, float)):
            raise ValueError("ProgressBar 'value' pointer must resolve to a number")
        return
    if component == "KeyValueList" and prop_name == "items":
        _KV_ITEMS_ADAPTER.validate_python(resolved)
        return
    if component == "DataTable" and prop_name == "rows":
        _TABLE_ROWS_ADAPTER.validate_python(resolved)
        return
    if component == "CheckList" and prop_name == "items":
        _CHECKLIST_ITEMS_ADAPTER.validate_python(resolved)
        return

    # String-valued pointer props
    max_lengths: dict[tuple[str, str], tuple[int, int]] = {
        ("Metric", "label"): (1, 80),
        ("Metric", "value"): (1, 64),
        ("Metric", "delta"): (0, 64),
        ("Metric", "caption"): (0, 200),
        ("Sparkline", "label"): (0, 80),
        ("ProgressBar", "label"): (0, 80),
        ("Badge", "text"): (1, 64),
        ("Text", "text"): (1, 280),
        ("Button", "label"): (1, 48),
        ("TextField", "label"): (0, 120),
        ("TextField", "value"): (0, 500),
        ("ChoiceGroup", "label"): (0, 200),
    }
    bounds = max_lengths.get((component, prop_name))
    if bounds is not None:
        min_len, max_len = bounds
        if resolved is None and min_len == 0:
            return
        if not isinstance(resolved, str):
            raise ValueError(
                f"{component}.{prop_name} pointer must resolve to a string, got {type(resolved).__name__}"
            )
        if len(resolved) < min_len or len(resolved) > max_len:
            raise ValueError(
                f"{component}.{prop_name} resolved string length {len(resolved)} "
                f"outside [{min_len}, {max_len}]"
            )


class A2UIComponentNode(BaseModel):
    """One node in the A2UI flat adjacency component list."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    component: A2UIComponentType
    props: dict[str, Any] = Field(default_factory=dict)
    children: list[str] = Field(default_factory=list, max_length=8)


class A2UISurfacePayload(BaseModel):
    """Validated A2UI v0.9 dashboard surface payload with decoupled layout and data_model."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["v0.9"] = "v0.9"
    catalog_id: A2UICatalogId = A2UI_PANEL_CATALOG_ID
    size: A2UISize = "md"
    icon: A2UIIcon | None = None
    root: str = Field(..., min_length=1, max_length=64)
    components: list[A2UIComponentNode] = Field(..., min_length=1, max_length=16)
    data_model: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_a2ui_grammar_and_bindings(self) -> A2UISurfacePayload:
        if len(self.components) > 12 and self.size == "md" and "size" not in self.model_fields_set:
            self.size = "lg"
        max_nodes = 16 if self.size == "lg" else 12
        if len(self.components) > max_nodes:
            raise ValueError(
                f"A2UI surface size={self.size!r} allows at most {max_nodes} components, "
                f"got {len(self.components)}"
            )

        by_id: dict[str, A2UIComponentNode] = {}
        for node in self.components:
            if node.id in by_id:
                raise ValueError(f"Duplicate A2UI component id: {node.id!r}")
            by_id[node.id] = node

        if self.root not in by_id:
            raise ValueError(f"A2UI root component {self.root!r} not found in components")

        heavy_visual_count = 0
        normalized_components: list[A2UIComponentNode] = []
        for node in self.components:
            prop_model = COMPONENT_PROP_MODELS[node.component]
            parsed_props = prop_model.model_validate(node.props)

            if node.component in _CONTAINER_COMPONENTS:
                if not node.children:
                    raise ValueError(
                        f"A2UI container {node.component!r} ({node.id!r}) must declare at least 1 child"
                    )
                if node.component == "Row" and len(node.children) > 4:
                    raise ValueError(
                        f"A2UI Row ({node.id!r}) allows at most 4 children, got {len(node.children)}"
                    )
            elif node.children:
                raise ValueError(
                    f"A2UI leaf component {node.component!r} ({node.id!r}) cannot have children"
                )

            if node.component in _HEAVY_VISUAL_COMPONENTS:
                heavy_visual_count += 1

            # Verify every JSON Pointer binding against data_model
            for field_name in parsed_props.model_fields_set | set(parsed_props.__class__.model_fields.keys()):
                val = getattr(parsed_props, field_name, None)
                if isinstance(val, A2UIPointer):
                    resolved = resolve_json_pointer(self.data_model, val.path)
                    _validate_resolved_pointer_value(node.component, field_name, resolved)

            normalized_components.append(
                A2UIComponentNode(
                    id=node.id,
                    component=node.component,
                    props=parsed_props.model_dump(exclude_none=True),
                    children=list(node.children),
                )
            )

        if self.size == "sm" and heavy_visual_count > 1:
            raise ValueError(
                "A2UI surface size='sm' allows at most 1 heavy visual (Sparkline or DataTable)"
            )

        # Graph traversal from root: check child existence, cycles, depth <= 3, and anti-squish rule
        visited: set[str] = set()
        visiting: set[str] = set()

        def _walk(node_id: str, depth: int) -> None:
            if depth > A2UI_MAX_TREE_DEPTH:
                raise ValueError(
                    f"A2UI component tree exceeds maximum depth of {A2UI_MAX_TREE_DEPTH} at {node_id!r}"
                )
            if node_id in visiting:
                raise ValueError(f"A2UI component graph contains a cycle at {node_id!r}")
            node = by_id[node_id]
            visiting.add(node_id)
            visited.add(node_id)

            if node.component == "Row" and len(node.children) > 1:
                for child_id in node.children:
                    child = by_id.get(child_id)
                    if child is not None and child.component == "DataTable":
                        raise ValueError(
                            f"A2UI anti-squish rule: DataTable ({child_id!r}) cannot be placed "
                            f"inside a multi-child Row ({node_id!r})"
                        )

            for child_id in node.children:
                if child_id not in by_id:
                    raise ValueError(
                        f"A2UI component {node_id!r} references missing child {child_id!r}"
                    )
                _walk(child_id, depth + 1)

            visiting.remove(node_id)

        _walk(self.root, 1)

        orphans = set(by_id.keys()) - visited
        if orphans:
            raise ValueError(
                f"A2UI component graph contains unreachable orphan components: {sorted(orphans)}"
            )

        self.components = normalized_components
        return self


#: Payload model for each widget type, used for type-matched validation.
WIDGET_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "a2ui_surface": A2UISurfacePayload,
}


def validate_widget_payload(widget_type: str, payload: dict[str, Any]) -> A2UISurfacePayload:
    """Parse a raw payload dict against the A2UI surface schema.

    Raises:
        ValueError: The widget type is unknown or the payload fails validation.
    """
    model = WIDGET_PAYLOAD_MODELS.get(widget_type)
    if model is None:
        raise ValueError(f"unknown widget_type: {widget_type!r}")
    return A2UISurfacePayload.model_validate(payload)


def truncate_a2ui_data_model(
    surface_payload: dict[str, Any],
    data_model: dict[str, Any],
) -> dict[str, Any]:
    """Cap pointer-bound arrays in ``data_model`` to each component's schema limit."""
    trimmed = deepcopy(data_model)
    components = surface_payload.get("components")
    if not isinstance(components, list):
        return trimmed

    caps: list[tuple[str, int]] = []
    for node in components:
        if not isinstance(node, dict):
            continue
        comp_type = node.get("component")
        props = node.get("props")
        if not isinstance(props, dict):
            continue
        if comp_type == "DataTable" and isinstance(props.get("rows"), dict):
            path = props["rows"].get("path")
            if isinstance(path, str):
                caps.append((path, A2UI_TABLE_MAX_ROWS))
        elif comp_type == "CheckList" and isinstance(props.get("items"), dict):
            path = props["items"].get("path")
            if isinstance(path, str):
                caps.append((path, A2UI_CHECKLIST_MAX_ITEMS))
        elif comp_type == "KeyValueList" and isinstance(props.get("items"), dict):
            path = props["items"].get("path")
            if isinstance(path, str):
                caps.append((path, A2UI_KV_MAX_ITEMS))
        elif comp_type == "Sparkline" and isinstance(props.get("points"), dict):
            path = props["points"].get("path")
            if isinstance(path, str):
                caps.append((path, A2UI_SPARKLINE_MAX_POINTS))

    for path, limit in caps:
        try:
            resolved = resolve_json_pointer(trimmed, path)
        except ValueError:
            continue
        if isinstance(resolved, list) and len(resolved) > limit:
            trimmed = set_json_pointer(trimmed, path, resolved[:limit])
    return trimmed


class A2UIDataModelPatch(BaseModel):
    """A2UI data-only update envelope (replaces or patches ``data_model`` without touching components)."""

    model_config = ConfigDict(extra="forbid")

    data_model: dict[str, Any] | None = None
    updateDataModel: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_one_source(self) -> A2UIDataModelPatch:
        has_dm = self.data_model is not None
        has_udm = self.updateDataModel is not None
        if has_dm == has_udm:
            raise ValueError(
                "A2UI data update requires either 'data_model' or 'updateDataModel'"
            )
        if self.updateDataModel is not None:
            allowed = {"surfaceId", "path", "value"}
            extra = set(self.updateDataModel.keys()) - allowed
            if extra or "value" not in self.updateDataModel:
                raise ValueError(
                    "updateDataModel must contain 'value' and optional 'path' / 'surfaceId'"
                )
        return self


def merge_a2ui_payload_update(
    existing_payload: dict[str, Any],
    incoming_payload: dict[str, Any],
    *,
    truncate: bool = False,
) -> A2UISurfacePayload:
    """Validate a full A2UI surface replacement or merge an A2UI ``updateDataModel`` patch.

     Keeps ``components`` frozen during data-only updates while verifying that every
    JSON Pointer binding in the existing layout resolves against the updated ``data_model``.
    """
    if not isinstance(incoming_payload, dict):
        raise ValueError("A2UI update payload must be a JSON object")

    if "components" in incoming_payload or "root" in incoming_payload:
        candidate = deepcopy(incoming_payload)
        if truncate and isinstance(candidate.get("data_model"), dict):
            candidate["data_model"] = truncate_a2ui_data_model(
                candidate, candidate["data_model"]
            )
        return A2UISurfacePayload.model_validate(candidate)

    patch = A2UIDataModelPatch.model_validate(incoming_payload)
    merged = deepcopy(existing_payload)
    current_data = deepcopy(merged.get("data_model") or {})

    if patch.data_model is not None:
        next_data = deepcopy(patch.data_model)
    else:
        assert patch.updateDataModel is not None
        pointer_path = str(patch.updateDataModel.get("path") or "/")
        next_data = set_json_pointer(
            current_data,
            pointer_path,
            patch.updateDataModel["value"],
        )

    if truncate:
        next_data = truncate_a2ui_data_model(merged, next_data)
    merged["data_model"] = next_data
    return A2UISurfacePayload.model_validate(merged)


class ConnectorQueryRefresh(BaseModel):
    """Refresh policy backed by one connector query."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["connector_query"] = "connector_query"
    connector_id: str = Field(..., min_length=1, max_length=64)
    query: str = Field(..., min_length=1, max_length=500)
    prompt_hint: str | None = Field(default=None, max_length=1000)


class StaticRefresh(BaseModel):
    """Refresh policy that only recomputes the next refresh time."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["static"] = "static"


RefreshConfig = Annotated[
    ConnectorQueryRefresh | StaticRefresh, Field(discriminator="kind")
]

RefreshInterval = Literal[15, 60, 240, 1440]


class WidgetCreateRequest(BaseModel):
    """Inbound request for creating a dashboard widget."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=120)
    widget_type: WidgetType = Field(default="a2ui_surface")
    data_payload: dict[str, Any] = Field(...)
    refresh: RefreshConfig | None = None
    refresh_interval_minutes: RefreshInterval | None = None


class WidgetUpdateRequest(BaseModel):
    """Inbound request for updating a dashboard widget's A2UI surface or data_model."""

    model_config = ConfigDict(extra="forbid")

    widget_id: UUID = Field(...)
    data_payload: dict[str, Any] = Field(...)


class WidgetResponse(BaseModel):
    """Outbound wire representation of one dashboard widget row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    title: str
    widget_type: WidgetType
    data_payload: dict[str, Any]
    refresh_config: dict[str, Any] | None = None
    refresh_interval_minutes: RefreshInterval | None = None
    next_refresh_at: datetime | None = None
    version: int
    created_at: datetime
    updated_at: datetime

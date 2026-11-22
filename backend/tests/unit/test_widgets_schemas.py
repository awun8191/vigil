"""Unit tests for the A2UI v0.9 widget Pydantic v2 schemas and grammar validator."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.modules.widgets.data import (
    A2UI_PANEL_CATALOG_ID,
    A2UISurfacePayload,
    ConnectorQueryRefresh,
    StaticRefresh,
    WidgetCreateRequest,
    WidgetUpdateRequest,
    merge_a2ui_payload_update,
    resolve_json_pointer,
    set_json_pointer,
    validate_widget_payload,
)


def make_metric_surface(value: str = "42", delta: str = "+3") -> dict[str, Any]:
    """Build a minimal valid A2UI metric surface payload."""
    return {
        "version": "v0.9",
        "catalog_id": A2UI_PANEL_CATALOG_ID,
        "size": "md",
        "icon": "activity",
        "root": "root_col",
        "components": [
            {
                "id": "root_col",
                "component": "Column",
                "props": {"gap": "normal"},
                "children": ["kpi_main"],
            },
            {
                "id": "kpi_main",
                "component": "Metric",
                "props": {
                    "label": "Active Users",
                    "value": {"path": "/summary/value"},
                    "delta": {"path": "/summary/delta"},
                    "tone": "positive",
                },
            },
        ],
        "data_model": {
            "summary": {
                "value": value,
                "delta": delta,
            }
        },
    }


def test_a2ui_metric_surface_accepts_good_input() -> None:
    """A valid A2UI surface with JSON Pointer bindings parses cleanly."""
    parsed = A2UISurfacePayload.model_validate(make_metric_surface("84.2 km", "+14%"))
    assert parsed.catalog_id == A2UI_PANEL_CATALOG_ID
    assert parsed.root == "root_col"
    assert parsed.data_model["summary"]["value"] == "84.2 km"


def test_a2ui_full_catalog_components_parse() -> None:
    """All 12 catalog primitives parse and validate their pointer bindings."""
    surface = {
        "size": "lg",
        "icon": "sparkles",
        "root": "col_root",
        "components": [
            {
                "id": "col_root",
                "component": "Column",
                "props": {"gap": "normal"},
                "children": [
                    "row_header",
                    "div_1",
                    "spark_1",
                    "prog_1",
                    "kv_1",
                    "tbl_1",
                    "chk_1",
                    "txt_1",
                    "btn_1",
                ],
            },
            {
                "id": "row_header",
                "component": "Row",
                "props": {"justify": "between"} if False else {"align": "between", "gap": "tight"},
                "children": ["kpi_1", "badge_1"],
            },
            {
                "id": "kpi_1",
                "component": "Metric",
                "props": {"label": "Distance", "value": {"path": "/kpi/val"}, "tone": "accent"},
            },
            {
                "id": "badge_1",
                "component": "Badge",
                "props": {"text": {"path": "/kpi/status"}, "tone": "positive"},
            },
            {"id": "div_1", "component": "Divider", "props": {}},
            {
                "id": "spark_1",
                "component": "Sparkline",
                "props": {"points": {"path": "/trend"}, "label": "7d Trend", "tone": "accent"},
            },
            {
                "id": "prog_1",
                "component": "ProgressBar",
                "props": {"label": "Monthly Goal", "value": {"path": "/goal_pct"}, "max": 100},
            },
            {
                "id": "kv_1",
                "component": "KeyValueList",
                "props": {"items": {"path": "/kv_items"}, "max_items": 3},
            },
            {
                "id": "tbl_1",
                "component": "DataTable",
                "props": {"columns": ["Line", "Status"], "rows": {"path": "/table_rows"}},
            },
            {
                "id": "chk_1",
                "component": "CheckList",
                "props": {"items": {"path": "/tasks"}},
            },
            {
                "id": "txt_1",
                "component": "Text",
                "props": {"text": "Updated automatically", "emphasis": "muted"},
            },
            {
                "id": "btn_1",
                "component": "Button",
                "props": {
                    "label": "Ask Vigil",
                    "variant": "secondary",
                    "action": {"functionCall": {"call": "ask_vigil", "prompt": "Explain trend"}},
                },
            },
        ],
        "data_model": {
            "kpi": {"val": "84.2 km", "status": "On Track"},
            "trend": [10.0, 12.5, 9.0, 14.2, 16.0],
            "goal_pct": 78.5,
            "kv_items": [{"label": "Northern", "value": "Good Service", "tone": "positive"}],
            "table_rows": [["Victoria", "Minor Delays"]],
            "tasks": [{"id": "t1", "label": "Log tempo run", "done": True}],
        },
    }
    # Column allows up to 8 children; let's verify >8 children on Column is rejected
    with pytest.raises(ValidationError):
        A2UISurfacePayload.model_validate(surface)

    # Trim col_root children to 8 by grouping txt_1 and btn_1 in a footer Row so all 12 catalog primitives parse
    surface["components"][0]["children"] = [
        "row_header",
        "div_1",
        "spark_1",
        "prog_1",
        "kv_1",
        "tbl_1",
        "chk_1",
        "row_footer",
    ]
    surface["components"].append(
        {
            "id": "row_footer",
            "component": "Row",
            "props": {"align": "between", "gap": "normal"},
            "children": ["txt_1", "btn_1"],
        }
    )
    parsed = A2UISurfacePayload.model_validate(surface)
    assert len(parsed.components) == 13


def test_legacy_non_a2ui_widget_types_are_rejected() -> None:
    """Legacy non-A2UI widget types (metric_card, data_table, task_list, status_feed) fail validation."""
    for legacy_type in ("metric_card", "data_table", "task_list", "status_feed", "chart"):
        with pytest.raises(ValueError, match="unknown widget_type"):
            validate_widget_payload(legacy_type, {"value": "1"})


def test_a2ui_rejects_unknown_props_and_raw_style_tokens() -> None:
    """extra=forbid and strict semantic enums reject raw CSS or unknown keys."""
    bad = make_metric_surface()
    bad["components"][1]["props"]["color"] = "#ff0000"
    with pytest.raises(ValidationError):
        A2UISurfacePayload.model_validate(bad)

    bad_tone = make_metric_surface()
    bad_tone["components"][1]["props"]["tone"] = "neon_pink"
    with pytest.raises(ValidationError):
        A2UISurfacePayload.model_validate(bad_tone)


def test_a2ui_rejects_cycle_in_component_graph() -> None:
    """A cycle in the flat adjacency list is rejected."""
    cyclic = {
        "root": "col_a",
        "components": [
            {"id": "col_a", "component": "Column", "children": ["col_b"]},
            {"id": "col_b", "component": "Column", "children": ["col_a"]},
        ],
        "data_model": {},
    }
    with pytest.raises(ValidationError, match="cycle"):
        A2UISurfacePayload.model_validate(cyclic)


def test_a2ui_rejects_orphan_components() -> None:
    """Components unreachable from root are rejected."""
    orphan_surface = make_metric_surface()
    orphan_surface["components"].append(
        {"id": "orphan_badge", "component": "Badge", "props": {"text": "Lost"}}
    )
    with pytest.raises(ValidationError, match="orphan"):
        A2UISurfacePayload.model_validate(orphan_surface)


def test_a2ui_rejects_tree_depth_over_three() -> None:
    """A component hierarchy deeper than 3 levels is rejected."""
    too_deep = {
        "root": "c1",
        "components": [
            {"id": "c1", "component": "Column", "children": ["c2"]},
            {"id": "c2", "component": "Column", "children": ["c3"]},
            {"id": "c3", "component": "Column", "children": ["leaf"]},
            {"id": "leaf", "component": "Badge", "props": {"text": "Too deep"}},
        ],
        "data_model": {},
    }
    with pytest.raises(ValidationError, match="maximum depth"):
        A2UISurfacePayload.model_validate(too_deep)


def test_a2ui_rejects_leaf_with_children_and_empty_container() -> None:
    """Leaf components cannot have children and containers cannot be empty."""
    leaf_with_child = {
        "root": "m1",
        "components": [
            {
                "id": "m1",
                "component": "Metric",
                "props": {"label": "L", "value": "1"},
                "children": ["b1"],
            },
            {"id": "b1", "component": "Badge", "props": {"text": "ok"}},
        ],
        "data_model": {},
    }
    with pytest.raises(ValidationError, match="leaf component"):
        A2UISurfacePayload.model_validate(leaf_with_child)

    empty_container = {
        "root": "c1",
        "components": [{"id": "c1", "component": "Column", "children": []}],
        "data_model": {},
    }
    with pytest.raises(ValidationError, match="at least 1 child"):
        A2UISurfacePayload.model_validate(empty_container)


def test_a2ui_anti_squish_rejects_datatable_inside_multi_child_row() -> None:
    """A DataTable inside a multi-child horizontal Row is rejected."""
    squished = {
        "root": "row_1",
        "components": [
            {"id": "row_1", "component": "Row", "children": ["kpi_1", "tbl_1"]},
            {"id": "kpi_1", "component": "Metric", "props": {"label": "A", "value": "1"}},
            {
                "id": "tbl_1",
                "component": "DataTable",
                "props": {"columns": ["C1"], "rows": [["R1"]]},
            },
        ],
        "data_model": {},
    }
    with pytest.raises(ValidationError, match="anti-squish"):
        A2UISurfacePayload.model_validate(squished)


def test_a2ui_rejects_unresolved_json_pointer() -> None:
    """A JSON Pointer that does not resolve in data_model is rejected."""
    broken = make_metric_surface()
    broken["data_model"] = {"summary": {}}
    with pytest.raises(ValidationError, match="could not resolve key"):
        A2UISurfacePayload.model_validate(broken)


def test_json_pointer_escapes_and_patch_merge() -> None:
    """RFC 6901 ~0 and ~1 escapes and data-only merge_a2ui_payload_update work."""
    data = {"a/b": {"c~d": ["first", "second"]}}
    assert resolve_json_pointer(data, "/a~1b/c~0d/1") == "second"
    mutated = set_json_pointer(data, "/a~1b/c~0d", ["updated"])
    assert resolve_json_pointer(mutated, "/a~1b/c~0d/0") == "updated"

    base = make_metric_surface("10", "+1")
    merged = merge_a2ui_payload_update(
        base,
        {"updateDataModel": {"path": "/summary/value", "value": "99"}},
    )
    assert merged.data_model["summary"]["value"] == "99"
    assert merged.data_model["summary"]["delta"] == "+1"
    assert len(merged.components) == 2


def test_refresh_kinds_parse() -> None:
    """Both refresh kinds parse with their required fields."""
    query = ConnectorQueryRefresh(connector_id="gmail", query="is:unread")
    assert query.kind == "connector_query"
    static = StaticRefresh()
    assert static.kind == "static"


def test_refresh_rejects_free_form_kind() -> None:
    """An unknown refresh kind is rejected."""
    with pytest.raises(ValidationError):
        ConnectorQueryRefresh.model_validate(
            {"kind": "sql_view", "connector_id": "gmail", "query": "q"}
        )


def test_create_request_rejects_user_id_and_bad_interval() -> None:
    """Identity never arrives in tool args and only allowlisted intervals parse."""
    with pytest.raises(ValidationError):
        WidgetCreateRequest.model_validate(
            {
                "title": "t",
                "widget_type": "a2ui_surface",
                "data_payload": make_metric_surface(),
                "user_id": str(uuid4()),
            }
        )
    with pytest.raises(ValidationError):
        WidgetCreateRequest.model_validate(
            {
                "title": "t",
                "widget_type": "a2ui_surface",
                "data_payload": make_metric_surface(),
                "refresh_interval_minutes": 5,
            }
        )


def test_update_request_rejects_user_id() -> None:
    """Identity never arrives in update request args."""
    with pytest.raises(ValidationError):
        WidgetUpdateRequest.model_validate(
            {
                "widget_id": str(uuid4()),
                "data_payload": {"data_model": {"summary": {"value": "2", "delta": "+1"}}},
                "user_id": str(uuid4()),
            }
        )

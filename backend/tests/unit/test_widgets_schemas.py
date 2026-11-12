"""Unit tests for the widget Pydantic v2 schemas.

No database is needed: every case validates the wire shapes and the
type-matched payload rule only.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.modules.widgets.data import (
    ConnectorQueryRefresh,
    StaticRefresh,
    validate_widget_payload,
)


def test_metric_payload_accepts_good_input() -> None:
    """A full metric payload parses."""
    from src.modules.widgets.data import MetricPayload

    payload = MetricPayload(value="42", delta="+3", caption="Active users")
    assert payload.value == "42"


def test_metric_payload_rejects_unknown_key() -> None:
    """extra=forbid rejects an unknown key."""
    from src.modules.widgets.data import MetricPayload

    with pytest.raises(ValidationError):
        MetricPayload.model_validate({"value": "42", "bogus": "no"})


def test_metric_payload_rejects_overlong_value() -> None:
    """The 64 char value cap rejects longer input."""
    from src.modules.widgets.data import MetricPayload

    with pytest.raises(ValidationError):
        MetricPayload.model_validate({"value": "x" * 65})


def test_table_payload_accepts_good_input() -> None:
    """A small table parses."""
    from src.modules.widgets.data import TablePayload

    payload = TablePayload.model_validate({"columns": ["a", "b"], "rows": [["1", "2"]]})
    assert payload.columns == ["a", "b"]


def test_table_payload_rejects_missing_columns() -> None:
    """At least one column is required."""
    from src.modules.widgets.data import TablePayload

    with pytest.raises(ValidationError):
        TablePayload.model_validate({"columns": [], "rows": []})


def test_table_payload_rejects_over_cap_rows() -> None:
    """More than 50 rows are rejected."""
    from src.modules.widgets.data import TablePayload

    with pytest.raises(ValidationError):
        TablePayload.model_validate({"columns": ["a"], "rows": [["x"]] * 51})


def test_table_payload_rejects_unknown_key() -> None:
    """extra=forbid rejects an unknown key."""
    from src.modules.widgets.data import TablePayload

    with pytest.raises(ValidationError):
        TablePayload.model_validate({"columns": ["a"], "rows": [], "extra_key": 1})


def test_task_list_payload_accepts_good_input() -> None:
    """A task list with two items parses."""
    from src.modules.widgets.data import TaskListPayload

    payload = TaskListPayload.model_validate(
        {"items": [{"id": "t1", "label": "First"}, {"id": "t2", "label": "Second", "done": True}]}
    )
    assert len(payload.items) == 2
    assert payload.items[0].done is False


def test_task_list_payload_rejects_over_cap_items() -> None:
    """More than 50 items are rejected."""
    from src.modules.widgets.data import TaskListPayload

    with pytest.raises(ValidationError):
        TaskListPayload.model_validate(
            {"items": [{"id": str(i), "label": "x"} for i in range(51)]}
        )


def test_task_list_payload_rejects_overlong_label() -> None:
    """The 200 char label cap rejects longer input."""
    from src.modules.widgets.data import TaskListPayload

    with pytest.raises(ValidationError):
        TaskListPayload.model_validate({"items": [{"id": "t1", "label": "x" * 201}]})


def test_feed_payload_accepts_good_input() -> None:
    """A status feed with one entry parses."""
    from src.modules.widgets.data import FeedPayload

    payload = FeedPayload.model_validate({"entries": [{"ts": "09:00", "text": "Booted"}]})
    assert payload.entries[0].text == "Booted"


def test_feed_payload_rejects_over_cap_entries() -> None:
    """More than 30 entries are rejected."""
    from src.modules.widgets.data import FeedPayload

    with pytest.raises(ValidationError):
        FeedPayload.model_validate(
            {"entries": [{"ts": "t", "text": "x"} for _ in range(31)]}
        )


def test_feed_payload_rejects_overlong_text() -> None:
    """The 280 char text cap rejects longer input."""
    from src.modules.widgets.data import FeedPayload

    with pytest.raises(ValidationError):
        FeedPayload.model_validate({"entries": [{"ts": "t", "text": "x" * 281}]})


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


def test_create_request_rejects_user_id() -> None:
    """Identity never arrives in tool args: user_id is forbidden."""
    from src.modules.widgets.data import WidgetCreateRequest

    with pytest.raises(ValidationError):
        WidgetCreateRequest.model_validate(
            {
                "title": "t",
                "widget_type": "metric_card",
                "data_payload": {"value": "1"},
                "user_id": str(uuid4()),
            }
        )


def test_create_request_rejects_bad_interval() -> None:
    """Only the allowlisted intervals parse."""
    from src.modules.widgets.data import WidgetCreateRequest

    with pytest.raises(ValidationError):
        WidgetCreateRequest.model_validate(
            {
                "title": "t",
                "widget_type": "metric_card",
                "data_payload": {"value": "1"},
                "refresh_interval_minutes": 5,
            }
        )


def test_validate_widget_payload_matches_each_type() -> None:
    """Each widget type accepts its own shape."""
    assert validate_widget_payload("metric_card", {"value": "1"}).model_dump() == {
        "value": "1",
        "delta": None,
        "caption": None,
    }
    assert validate_widget_payload("data_table", {"columns": ["a"]}).model_dump()["columns"] == ["a"]
    assert validate_widget_payload("task_list", {"items": []}).model_dump()["items"] == []
    assert validate_widget_payload("status_feed", {"entries": []}).model_dump()["entries"] == []


def test_validate_widget_payload_rejects_mismatch() -> None:
    """A table payload fails the metric_card shape."""
    with pytest.raises(ValidationError):
        validate_widget_payload("metric_card", {"columns": ["a"], "rows": []})


def test_validate_widget_payload_rejects_unknown_type() -> None:
    """An unknown widget type fails before any shape check."""
    with pytest.raises(ValueError, match="unknown widget_type"):
        validate_widget_payload("chart", {"value": "1"})


def test_update_request_rejects_user_id() -> None:
    """Identity never arrives in tool args: user_id is forbidden."""
    from src.modules.widgets.data import WidgetUpdateRequest

    with pytest.raises(ValidationError):
        WidgetUpdateRequest.model_validate(
            {
                "widget_id": str(uuid4()),
                "data_payload": {"value": "1"},
                "user_id": str(uuid4()),
            }
        )

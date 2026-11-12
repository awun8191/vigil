"""Unit tests for the refresh_widget queue priority registration."""
from src.modules.jobs.service import JOB_TYPE_PRIORITIES, priority_for_job_type


def test_refresh_widget_priority_is_40() -> None:
    """The queue ranks refresh_widget below index_memory and above poll_connector."""
    assert JOB_TYPE_PRIORITIES["refresh_widget"] == 40


def test_refresh_widget_priority_ordering() -> None:
    """refresh_widget claims after index_memory and before poll_connector."""
    assert JOB_TYPE_PRIORITIES["index_memory"] == 50
    assert priority_for_job_type("refresh_widget") == 40
    assert JOB_TYPE_PRIORITIES["poll_connector"] == 20
    ordered = sorted(JOB_TYPE_PRIORITIES.items(), key=lambda item: item[1], reverse=True)
    names = [name for name, _ in ordered]
    assert names.index("index_memory") < names.index("refresh_widget")
    assert names.index("refresh_widget") < names.index("poll_connector")

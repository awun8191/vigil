"""Unit tests for the sub-agent safety rules: depth, workspace, budget, cancel.

The service enforces the rules and the journals hold the tree. No network,
no container and no provider key are involved.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.modules.runs.repository import RunCreateDTO
from src.modules.runs.service import RunsService
from src.modules.runs.state import InMemoryRunJournal, MaxDepthExceeded
from tests.unit.loop_fakes import agent_config


def make_service(
    journal: InMemoryRunJournal | None = None,
    **settings_kwargs: object,
) -> tuple[RunsService, InMemoryRunJournal]:
    """Build a service over a fresh in-memory journal."""
    journal = journal if journal is not None else InMemoryRunJournal()
    service = RunsService(
        session=None,
        settings=Settings(APP_ENV="test", **settings_kwargs),  # type: ignore[arg-type]
        journal=journal,
    )
    return service, journal


async def test_depth_limit_enforced() -> None:
    """A child past MAX_RUN_DEPTH is rejected with a clear error."""
    service, _ = make_service()
    config = agent_config()
    await service.save_agent_config(config)
    parent = await service.create_run(RunCreateDTO(config_id=config.id))
    child = await service.create_child_run(parent.id)
    assert child.depth == 1
    grandchild = await service.create_child_run(child.id)
    assert grandchild.depth == 2
    with pytest.raises(MaxDepthExceeded, match="MAX_RUN_DEPTH"):
        await service.create_child_run(grandchild.id)


async def test_child_workspace_isolated() -> None:
    """A child owns a workspace ref that differs from the parent."""
    service, journal = make_service()
    config = agent_config()
    await service.save_agent_config(config)
    parent = await service.create_run(RunCreateDTO(config_id=config.id))
    child = await service.create_child_run(parent.id)
    parent_ref = await journal.get_workspace_ref(parent.id)
    child_ref = await journal.get_workspace_ref(child.id)
    assert parent_ref is not None
    assert child_ref is not None
    assert child_ref != parent_ref


async def test_child_cost_charges_parent() -> None:
    """Two children that together exceed the parent limit fail the parent."""
    service, _ = make_service()
    config = agent_config(limits={"max_cost_micros": 1_000})
    await service.save_agent_config(config)
    parent = await service.create_run(RunCreateDTO(config_id=config.id))
    first = await service.create_child_run(parent.id)
    second = await service.create_child_run(parent.id)
    await service.record_cost_micros(first.id, 600)
    assert (await service.get_run_by_id(parent.id)).status != "failed"  # type: ignore[union-attr]
    await service.record_cost_micros(second.id, 600)
    updated = await service.get_run_by_id(parent.id)
    assert updated is not None
    assert updated.status == "failed"


async def test_cancel_walks_tree() -> None:
    """Cancelling a parent cancels both children."""
    service, _ = make_service()
    config = agent_config()
    await service.save_agent_config(config)
    parent = await service.create_run(RunCreateDTO(config_id=config.id))
    first = await service.create_child_run(parent.id)
    second = await service.create_child_run(parent.id)
    await service.cancel_run(parent.id)
    for run_id in (parent.id, first.id, second.id):
        run = await service.get_run_by_id(run_id)
        assert run is not None
        assert run.status == "failed"


async def test_parent_cycle_impossible_by_construction() -> None:
    """A child id is minted fresh under an existing parent, so no cycle fits."""
    service, _ = make_service()
    config = agent_config()
    await service.save_agent_config(config)
    parent = await service.create_run(RunCreateDTO(config_id=config.id))
    child = await service.create_child_run(parent.id)
    assert child.id != parent.id
    assert child.parent_run_id == parent.id
    assert await service._ancestor_ids(child.id) == [parent.id]
    # A caller-supplied depth never wins: the parent dictates the depth.
    coerced = await service.create_run(
        RunCreateDTO(config_id=config.id, parent_run_id=parent.id, depth=0)
    )
    assert coerced.depth == parent.depth + 1
    # A parent that has no row cannot anchor a child.
    with pytest.raises(NotFoundError, match="Parent run"):
        await service.create_child_run(uuid4())

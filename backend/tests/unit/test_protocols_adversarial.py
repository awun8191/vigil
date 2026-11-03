"""Adversarial challenge tests for Milestone 3 repository protocol purity, AST validation, and duck typing.

Verifies:
1. AST Validator Stress:
   - Genuinely catches illegal imports (from sqlalchemy import select, import httpx, asyncpg, etc.).
   - Genuinely catches raw SQL constructor calls (select, text, execute, etc.).
   - Enforces @runtime_checkable and Protocol inheritance on all repository classes.
   - Enforces pure method bodies (rejecting return, assignments, calls, raises, awaits, loops).
2. Protocol Duck Typing Robustness:
   - All 19 protocols (14 domain + 5 integrations) are @runtime_checkable.
   - Empty classes return False on isinstance.
   - Fully implemented classes return True on isinstance.
   - Classes missing even a single method return False across all 100 protocol methods.
   - Classes with None-valued attributes return False across all 100 protocol methods.
   - Candidate classes with extra methods still satisfy duck typing.
3. Cross-Protocol Disjointness:
   - Zero accidental subtyping or cross-protocol confusion across all 19 implementations.
"""
from __future__ import annotations

import ast
from pathlib import Path
import pytest

import tests.unit.test_protocols as tp
from src.config.settings import get_settings
from src.dependencies import (
    get_health_service,
    get_auth_service,
    get_cards_service,
    get_triage_service,
    get_runs_service,
    get_approvals_service,
    get_chat_service,
    get_artifacts_service,
    get_memory_service,
    get_compaction_service,
    get_skills_service,
    get_connectors_service,
    get_settings_service,
    get_jobs_service,
    get_jev_client,
    get_llm_client,
    get_sandbox_runner,
    get_mcp_host,
    get_notification_dispatcher,
)
from src.integrations.repositories import (
    JevClientProtocol,
    LLMClientProtocol,
    SandboxRunnerProtocol,
    MCPHostProtocol,
    NotificationDispatcherProtocol,
)
from src.modules.health.repository import HealthRepository
from src.modules.auth.repository import AuthRepository
from src.modules.cards.repository import CardsRepository
from src.modules.triage.repository import TriageRepository
from src.modules.runs.repository import RunsRepository
from src.modules.approvals.repository import ApprovalsRepository
from src.modules.chat.repository import ChatRepository
from src.modules.artifacts.repository import ArtifactsRepository
from src.modules.memory.repository import MemoryRepository
from src.modules.compaction.repository import CompactionRepository
from src.modules.skills.repository import SkillsRepository
from src.modules.connectors.repository import ConnectorsRepository
from src.modules.settings.repository import SettingsRepository
from src.modules.jobs.repository import JobsRepository


ALL_19_PROTOCOLS = [
    # 5 integration protocols
    JevClientProtocol,
    LLMClientProtocol,
    SandboxRunnerProtocol,
    MCPHostProtocol,
    NotificationDispatcherProtocol,
    # 14 domain protocols
    HealthRepository,
    AuthRepository,
    CardsRepository,
    TriageRepository,
    RunsRepository,
    ApprovalsRepository,
    ChatRepository,
    ArtifactsRepository,
    MemoryRepository,
    CompactionRepository,
    SkillsRepository,
    ConnectorsRepository,
    SettingsRepository,
    JobsRepository,
]


def _make_ast_tree(snippet: str) -> list[tuple[Path, ast.Module]]:
    p = Path("src/modules/adversarial_dummy/repository.py")
    return [(p, ast.parse(snippet, filename=str(p)))]


class TestASTValidatorStress:
    """Stress tests asserting that the AST validator detects any impurity or violation."""

    @pytest.mark.parametrize(
        "illegal_code,description",
        [
            ("from sqlalchemy import select", "sqlalchemy select import"),
            ("import httpx", "httpx import"),
            ("import sqlalchemy", "bare sqlalchemy import"),
            ("import sqlalchemy.orm as orm", "aliased sqlalchemy subpackage"),
            ("from httpx import AsyncClient", "httpx member import"),
            ("from asyncpg import create_pool", "asyncpg import"),
            ("from sqlmodel import Field", "sqlmodel import"),
            ("import tortoise", "tortoise import"),
            ("from aiohttp import ClientSession", "aiohttp import"),
            ("import requests", "requests import"),
            ("from src.schemas import User", "src.schemas import"),
            ("from src.schemas.auth import Device", "src.schemas subpath import"),
            ("from urllib.request import urlopen", "urllib.request import"),
            ("import sqlite3", "sqlite3 import"),
            ("import psycopg2", "psycopg2 import"),
            ("import peewee", "peewee import"),
            ("import databases", "databases import"),
        ],
    )
    def test_catches_forbidden_imports(self, illegal_code: str, description: str) -> None:
        tree = _make_ast_tree(illegal_code)
        with pytest.raises(AssertionError, match="Found forbidden SQL/ORM/HTTP imports"):
            tp.test_zero_forbidden_imports_in_repositories(tree)

    def test_permits_clean_imports(self) -> None:
        clean_code = (
            "from __future__ import annotations\n"
            "from typing import Protocol, runtime_checkable, Any\n"
            "from dataclasses import dataclass\n"
            "import uuid\n"
            "from datetime import datetime\n"
        )
        tree = _make_ast_tree(clean_code)
        # Should not raise
        tp.test_zero_forbidden_imports_in_repositories(tree)

    @pytest.mark.parametrize(
        "illegal_code,description",
        [
            ("def run():\n    return select(User)", "bare select() call"),
            ("def run(session):\n    return session.execute(text('SELECT 1'))", "execute() and text() calls"),
            ("def run():\n    return insert(User).values(id=1)", "insert() call"),
            ("def run():\n    return update(User).where(User.id == 1)", "update() call"),
            ("def run():\n    return delete(User)", "delete() call"),
            ("def run(res):\n    return res.scalars().all()", "scalars() call"),
        ],
    )
    def test_catches_forbidden_sql_calls(self, illegal_code: str, description: str) -> None:
        tree = _make_ast_tree(illegal_code)
        with pytest.raises(AssertionError, match="Found forbidden database calls"):
            tp.test_zero_sql_calls_in_repositories(tree)

    @pytest.mark.parametrize(
        "illegal_code,expected_error",
        [
            ("class BadRepo:\n    pass", "does not inherit from Protocol"),
            ("class BadRepo(Protocol):\n    pass", "missing @runtime_checkable"),
            ("@runtime_checkable\nclass BadRepo:\n    pass", "does not inherit from Protocol"),
            ("@runtime_checkable\nclass BadRepo(object):\n    pass", "does not inherit from Protocol"),
        ],
    )
    def test_catches_invalid_protocol_definitions(self, illegal_code: str, expected_error: str) -> None:
        tree = _make_ast_tree(illegal_code)
        with pytest.raises(AssertionError, match=expected_error):
            tp.test_all_repository_classes_are_protocols(tree)

    @pytest.mark.parametrize(
        "illegal_code,description",
        [
            (
                "@runtime_checkable\nclass R(Protocol):\n    def get(self):\n        return 42",
                "return statement",
            ),
            (
                "@runtime_checkable\nclass R(Protocol):\n    def get(self):\n        x = 1",
                "assignment statement",
            ),
            (
                "@runtime_checkable\nclass R(Protocol):\n    def get(self):\n        print('bad')",
                "function call statement",
            ),
            (
                "@runtime_checkable\nclass R(Protocol):\n    def get(self):\n        raise NotImplementedError",
                "raise statement",
            ),
            (
                "@runtime_checkable\nclass R(Protocol):\n    async def fetch(self):\n        await something()",
                "await statement",
            ),
            (
                "@runtime_checkable\nclass R(Protocol):\n    def compute(self):\n        \"\"\"Docstring\"\"\"\n        return 1 + 2",
                "docstring followed by return",
            ),
            (
                "@runtime_checkable\nclass R(Protocol):\n    def loop(self):\n        for i in range(10):\n            pass",
                "for loop",
            ),
            (
                "@runtime_checkable\nclass R(Protocol):\n    def branch(self):\n        if True:\n            pass",
                "if branch",
            ),
        ],
    )
    def test_catches_method_body_implementations(self, illegal_code: str, description: str) -> None:
        tree = _make_ast_tree(illegal_code)
        with pytest.raises(AssertionError, match="Found implementation logic inside protocol methods"):
            tp.test_protocol_methods_have_empty_bodies(tree)

    def test_permits_clean_protocol_methods(self) -> None:
        clean_code = (
            "@runtime_checkable\n"
            "class CleanRepo(Protocol):\n"
            "    def method_ellipsis(self) -> None:\n"
            "        ...\n"
            "    def method_pass(self) -> None:\n"
            "        pass\n"
            "    async def async_method(self) -> str:\n"
            "        \"\"\"Async method docstring.\"\"\"\n"
            "        ...\n"
            "    def doc_only(self) -> None:\n"
            "        \"\"\"Docstring only.\"\"\"\n"
        )
        tree = _make_ast_tree(clean_code)
        # Should not raise
        tp.test_protocol_methods_have_empty_bodies(tree)


class TestProtocolDuckTypingRobustness:
    """Stress tests verifying runtime duck typing on all 19 protocols."""

    def test_all_19_protocols_have_runtime_checkable_metadata(self) -> None:
        assert len(ALL_19_PROTOCOLS) == 19
        for proto in ALL_19_PROTOCOLS:
            assert getattr(proto, "_is_protocol", False), f"{proto.__name__} missing _is_protocol"
            assert getattr(proto, "_is_runtime_protocol", False), f"{proto.__name__} missing _is_runtime_protocol"

    def test_empty_candidate_fails_all_19_protocols(self) -> None:
        class EmptyCandidate:
            pass

        instance = EmptyCandidate()
        for proto in ALL_19_PROTOCOLS:
            assert not isinstance(instance, proto), f"Empty candidate should NOT satisfy {proto.__name__}"

    def test_complete_candidate_satisfies_all_19_protocols(self) -> None:
        for proto in ALL_19_PROTOCOLS:
            attrs = list(proto.__protocol_attrs__)
            impl_dict = {attr: (lambda self, *a, **kw: None) for attr in attrs}
            candidate_cls = type(f"Complete_{proto.__name__}", (), impl_dict)
            assert isinstance(candidate_cls(), proto), f"Complete implementation failed for {proto.__name__}"

    def test_single_method_omission_fails_isinstance_for_every_method(self) -> None:
        """Every single protocol method omission must cause isinstance check to return False."""
        total_tested = 0
        for proto in ALL_19_PROTOCOLS:
            attrs = list(proto.__protocol_attrs__)
            assert len(attrs) > 0, f"Protocol {proto.__name__} has zero attributes"
            for omitted in attrs:
                partial_dict = {
                    attr: (lambda self, *a, **kw: None)
                    for attr in attrs
                    if attr != omitted
                }
                partial_cls = type(f"Missing_{omitted}_{proto.__name__}", (), partial_dict)
                assert not isinstance(partial_cls(), proto), (
                    f"Duck typing failed: class missing '{omitted}' still satisfied {proto.__name__}"
                )
                total_tested += 1

        # Exactly 101 method omission cases across all 19 protocols
        assert total_tested == 101, f"Expected 101 method omission tests, got {total_tested}"

    def test_none_valued_attribute_fails_isinstance_for_every_method(self) -> None:
        """Setting any protocol member to None must cause isinstance check to return False."""
        total_tested = 0
        for proto in ALL_19_PROTOCOLS:
            attrs = list(proto.__protocol_attrs__)
            for none_attr in attrs:
                none_dict = {
                    attr: (lambda self, *a, **kw: None)
                    for attr in attrs
                    if attr != none_attr
                }
                none_dict[none_attr] = None
                none_cls = type(f"None_{none_attr}_{proto.__name__}", (), none_dict)
                assert not isinstance(none_cls(), proto), (
                    f"Duck typing failed: class with None for '{none_attr}' still satisfied {proto.__name__}"
                )
                total_tested += 1

        assert total_tested == 101, f"Expected 101 None-attribute tests, got {total_tested}"

    def test_extra_methods_do_not_break_duck_typing(self) -> None:
        for proto in ALL_19_PROTOCOLS:
            attrs = list(proto.__protocol_attrs__)
            extra_dict = {attr: (lambda self, *a, **kw: None) for attr in attrs}
            extra_dict["extraneous_custom_method"] = lambda self: "extra"
            extra_dict["extraneous_property"] = 999
            extra_cls = type(f"Extra_{proto.__name__}", (), extra_dict)
            assert isinstance(extra_cls(), proto), f"Extra methods broke duck typing for {proto.__name__}"


class TestProtocolIsolationAndDisjointness:
    """Verifies all concrete worker implementations satisfy their protocols with zero cross-contamination."""

    def test_all_14_domain_services_satisfy_their_respective_protocols(self) -> None:
        settings = get_settings()
        session = None

        pairs = [
            (get_health_service(session, settings), HealthRepository),
            (get_auth_service(session, settings), AuthRepository),
            (get_cards_service(session, settings), CardsRepository),
            (get_triage_service(session, settings), TriageRepository),
            (get_runs_service(session, settings), RunsRepository),
            (get_approvals_service(session, settings), ApprovalsRepository),
            (get_chat_service(session, settings), ChatRepository),
            (get_artifacts_service(session, settings), ArtifactsRepository),
            (get_memory_service(session, settings), MemoryRepository),
            (get_compaction_service(session, settings), CompactionRepository),
            (get_skills_service(session, settings), SkillsRepository),
            (get_connectors_service(session, settings), ConnectorsRepository),
            (get_settings_service(session, settings), SettingsRepository),
            (get_jobs_service(session, settings), JobsRepository),
        ]

        for svc, proto in pairs:
            assert isinstance(svc, proto), f"Service {type(svc).__name__} does not satisfy {proto.__name__}"

    def test_all_5_integration_clients_satisfy_their_respective_protocols(self) -> None:
        settings = get_settings()

        pairs = [
            (get_jev_client(settings), JevClientProtocol),
            (get_llm_client(settings), LLMClientProtocol),
            (get_sandbox_runner(settings), SandboxRunnerProtocol),
            (get_mcp_host(settings), MCPHostProtocol),
            (get_notification_dispatcher(settings), NotificationDispatcherProtocol),
        ]

        for client, proto in pairs:
            assert isinstance(client, proto), f"Client {type(client).__name__} does not satisfy {proto.__name__}"

    def test_domain_services_14x14_disjointness_matrix(self) -> None:
        """Ensure no domain service accidentally satisfies another domain module's repository protocol."""
        settings = get_settings()
        session = None

        domain_services = [
            ("health", get_health_service(session, settings), HealthRepository),
            ("auth", get_auth_service(session, settings), AuthRepository),
            ("cards", get_cards_service(session, settings), CardsRepository),
            ("triage", get_triage_service(session, settings), TriageRepository),
            ("runs", get_runs_service(session, settings), RunsRepository),
            ("approvals", get_approvals_service(session, settings), ApprovalsRepository),
            ("chat", get_chat_service(session, settings), ChatRepository),
            ("artifacts", get_artifacts_service(session, settings), ArtifactsRepository),
            ("memory", get_memory_service(session, settings), MemoryRepository),
            ("compaction", get_compaction_service(session, settings), CompactionRepository),
            ("skills", get_skills_service(session, settings), SkillsRepository),
            ("connectors", get_connectors_service(session, settings), ConnectorsRepository),
            ("settings", get_settings_service(session, settings), SettingsRepository),
            ("jobs", get_jobs_service(session, settings), JobsRepository),
        ]

        accidental_matches = []
        for name_a, svc_a, proto_a in domain_services:
            for name_b, _, proto_b in domain_services:
                is_match = isinstance(svc_a, proto_b)
                if name_a == name_b:
                    assert is_match, f"{name_a} failed to match {proto_a.__name__}"
                else:
                    if is_match:
                        accidental_matches.append((name_a, proto_b.__name__))

        assert not accidental_matches, f"Accidental domain cross-protocol matches: {accidental_matches}"

    def test_integration_clients_5x5_disjointness_matrix(self) -> None:
        """Ensure no integration client accidentally satisfies another integration protocol."""
        settings = get_settings()

        integration_clients = [
            ("jev", get_jev_client(settings), JevClientProtocol),
            ("llm", get_llm_client(settings), LLMClientProtocol),
            ("sandbox", get_sandbox_runner(settings), SandboxRunnerProtocol),
            ("mcp", get_mcp_host(settings), MCPHostProtocol),
            ("notification", get_notification_dispatcher(settings), NotificationDispatcherProtocol),
        ]

        accidental_matches = []
        for name_a, client_a, proto_a in integration_clients:
            for name_b, _, proto_b in integration_clients:
                is_match = isinstance(client_a, proto_b)
                if name_a == name_b:
                    assert is_match, f"{name_a} failed to match {proto_a.__name__}"
                else:
                    if is_match:
                        accidental_matches.append((name_a, proto_b.__name__))

        assert not accidental_matches, f"Accidental integration cross-protocol matches: {accidental_matches}"


class TestDTOImmutability:
    """Verifies that every DTO across all repository files is immutable (frozen)."""

    def test_all_dtos_are_frozen(self) -> None:
        import inspect
        from dataclasses import is_dataclass
        from pydantic import BaseModel

        import src.integrations.repositories as integ_repo
        import src.modules.health.repository as health_repo
        import src.modules.auth.repository as auth_repo
        import src.modules.cards.repository as cards_repo
        import src.modules.triage.repository as triage_repo
        import src.modules.runs.repository as runs_repo
        import src.modules.approvals.repository as approvals_repo
        import src.modules.chat.repository as chat_repo
        import src.modules.artifacts.repository as artifacts_repo
        import src.modules.memory.repository as memory_repo
        import src.modules.compaction.repository as compaction_repo
        import src.modules.skills.repository as skills_repo
        import src.modules.connectors.repository as connectors_repo
        import src.modules.settings.repository as settings_repo
        import src.modules.jobs.repository as jobs_repo

        modules = [
            integ_repo, health_repo, auth_repo, cards_repo, triage_repo, runs_repo,
            approvals_repo, chat_repo, artifacts_repo, memory_repo, compaction_repo,
            skills_repo, connectors_repo, settings_repo, jobs_repo,
        ]

        dto_count = 0
        for mod in modules:
            for name, obj in inspect.getmembers(mod, inspect.isclass):
                if obj.__module__ == mod.__name__ and name.endswith(("DTO", "Request", "Response", "Model")):
                    dto_count += 1
                    if is_dataclass(obj):
                        assert obj.__dataclass_params__.frozen, f"Dataclass {name} in {mod.__name__} is NOT frozen!"
                    elif issubclass(obj, BaseModel):
                        assert obj.model_config.get("frozen", False), f"Pydantic model {name} in {mod.__name__} is NOT frozen!"
                    else:
                        raise AssertionError(f"Class {name} in {mod.__name__} is neither dataclass nor BaseModel!")

        # A floor, not an exact total. The property under test is that every DTO
        # is frozen; the count only proves the inspection reached the modules.
        # An exact number turns a DTO that legitimately moves into a repository
        # module (MCPServerDTO did) into a red gate for no property.
        assert dto_count >= 52, f"Expected at least 52 DTOs across repository files, found {dto_count}"


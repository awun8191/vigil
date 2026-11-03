"""
Architectural AST test asserting that all repository files maintain pure protocol boundaries.

Invariants Enforced:
1. Zero SQL/ORM imports (sqlalchemy, sqlmodel, tortoise, asyncpg, src.schemas).
2. Zero HTTP network client imports (httpx, requests, aiohttp).
3. Every repository class is decorated with @runtime_checkable and inherits from Protocol.
4. Protocol methods contain only empty bodies (pass, docstring, or ellipsis '...').
"""
from __future__ import annotations

import ast
from pathlib import Path
import pytest

# Forbidden import modules
FORBIDDEN_IMPORT_PREFIXES = {
    "sqlalchemy",
    "sqlmodel",
    "tortoise",
    "asyncpg",
    "databases",
    "peewee",
    "psycopg",
    "psycopg2",
    "sqlite3",
    "src.schemas",
    "httpx",
    "requests",
    "aiohttp",
    "urllib",
}

FORBIDDEN_CALL_NAMES = {
    "select",
    "insert",
    "update",
    "delete",
    "text",
    "execute",
    "scalars",
}


def get_repository_files() -> list[Path]:
    """Discover all domain repository.py files and the central integrations/repositories.py."""
    backend_root = Path(__file__).parents[2]
    src_dir = backend_root / "src"

    repo_files: list[Path] = []

    # 1. Integrations repositories
    integrations_repo = src_dir / "integrations" / "repositories.py"
    if integrations_repo.is_file():
        repo_files.append(integrations_repo)

    # 2. Domain module repositories
    modules_dir = src_dir / "modules"
    if modules_dir.is_dir():
        for mod_dir in sorted(modules_dir.iterdir()):
            if mod_dir.is_dir() and not mod_dir.name.startswith("__"):
                repo_file = mod_dir / "repository.py"
                if repo_file.is_file():
                    repo_files.append(repo_file)

    return repo_files


@pytest.fixture(scope="module")
def repo_ast_trees() -> list[tuple[Path, ast.Module]]:
    """Parse all discovered repository files into AST modules."""
    files = get_repository_files()
    # Every module with a repository protocol, plus the integrations set. The
    # number moves when a module is added; the point of the guard is that the
    # discovery found them all rather than silently none.
    assert len(files) == 17, f"Expected exactly 17 repository files (16 modules + integrations), found {len(files)}"
    trees = []
    for f in files:
        content = f.read_text(encoding="utf-8")
        trees.append((f, ast.parse(content, filename=str(f))))
    return trees


def test_zero_forbidden_imports_in_repositories(repo_ast_trees: list[tuple[Path, ast.Module]]) -> None:
    """Verify zero SQL, ORM, or HTTP imports exist in any repository.py file."""
    violations: list[str] = []

    for path, tree in repo_ast_trees:
        rel_path = path.relative_to(path.parents[2])
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in FORBIDDEN_IMPORT_PREFIXES:
                        if alias.name == forbidden or alias.name.startswith(f"{forbidden}."):
                            violations.append(
                                f"{rel_path}:{node.lineno} imports forbidden module '{alias.name}'"
                            )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for forbidden in FORBIDDEN_IMPORT_PREFIXES:
                        if node.module == forbidden or node.module.startswith(f"{forbidden}."):
                            violations.append(
                                f"{rel_path}:{node.lineno} imports from forbidden module '{node.module}'"
                            )

    assert not violations, "Found forbidden SQL/ORM/HTTP imports in repository files:\n" + "\n".join(violations)


def test_zero_sql_calls_in_repositories(repo_ast_trees: list[tuple[Path, ast.Module]]) -> None:
    """Verify zero raw SQL statement constructor calls (select, text, execute) in repositories."""
    violations: list[str] = []

    for path, tree in repo_ast_trees:
        rel_path = path.relative_to(path.parents[2])
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                if func_name in FORBIDDEN_CALL_NAMES:
                    violations.append(
                        f"{rel_path}:{node.lineno} calls forbidden database construct '{func_name}()'"
                    )

    assert not violations, "Found forbidden database calls in repository files:\n" + "\n".join(violations)


def test_all_repository_classes_are_protocols(repo_ast_trees: list[tuple[Path, ast.Module]]) -> None:
    """Verify classes ending with 'Repository' or 'Protocol' inherit from Protocol and use @runtime_checkable."""
    violations: list[str] = []

    for path, tree in repo_ast_trees:
        rel_path = path.relative_to(path.parents[2])
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                # Skip DTOs (classes ending in DTO, Request, Response, Model)
                if node.name.endswith(("DTO", "Request", "Response", "Model", "Schema")):
                    continue

                # Must inherit from Protocol
                base_names = []
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        base_names.append(base.id)
                    elif isinstance(base, ast.Attribute):
                        base_names.append(base.attr)

                if "Protocol" not in base_names:
                    violations.append(
                        f"{rel_path}:{node.lineno} class '{node.name}' does not inherit from Protocol"
                    )

                # Must have @runtime_checkable decorator
                decorator_names = []
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Name):
                        decorator_names.append(dec.id)
                    elif isinstance(dec, ast.Attribute):
                        decorator_names.append(dec.attr)

                if "runtime_checkable" not in decorator_names:
                    violations.append(
                        f"{rel_path}:{node.lineno} class '{node.name}' is missing @runtime_checkable"
                    )

    assert not violations, "Repository protocol definition errors:\n" + "\n".join(violations)


def test_protocol_methods_have_empty_bodies(repo_ast_trees: list[tuple[Path, ast.Module]]) -> None:
    """Verify protocol methods contain zero implementation logic (only pass, docstring, or ellipsis)."""
    violations: list[str] = []

    for path, tree in repo_ast_trees:
        rel_path = path.relative_to(path.parents[2])
        for class_node in tree.body:
            if isinstance(class_node, ast.ClassDef):
                base_names = [
                    b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else ""
                    for b in class_node.bases
                ]
                if "Protocol" not in base_names:
                    continue

                for member in class_node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        # Allowed body statements: docstring (ast.Expr with Constant), pass, or ellipsis
                        for stmt in member.body:
                            if isinstance(stmt, ast.Pass):
                                continue
                            if isinstance(stmt, ast.Expr):
                                if isinstance(stmt.value, ast.Constant):
                                    # Docstring or Ellipsis (...)
                                    continue
                            violations.append(
                                f"{rel_path}:{member.lineno} method '{class_node.name}.{member.name}' "
                                f"contains implementation statement ({type(stmt).__name__}) instead of '...'"
                            )

    assert not violations, "Found implementation logic inside protocol methods:\n" + "\n".join(violations)

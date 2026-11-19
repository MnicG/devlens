"""Testes de segurança da Fase 1 (docs/05 seção 9, docs/06 seção 13).

Cobrem: path traversal, link simbólico externo, `.env`, arquivo grande e
binário, redação nas saídas e nos bancos, conteúdo hostil (injeção) tratado
como dado e hooks/configuração maliciosos do repositório analisado.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from app.evidence.store import EvidenceStore
from app.evidence.verify import verify_all
from app.index.store import IndexStore
from app.schemas.contracts import ToolResult
from app.tools.base import ToolCache, ToolContext, ToolRunner, error_code
from app.tools.registry import default_runner
from app.tools.repo_source import LocalRepoSource
from conftest import (
    HMAC_KEY,
    SYNTHETIC_SECRETS,
    FixtureRepo,
    make_executable,
)
from git import Actor, Repo


@pytest.fixture
def runner() -> ToolRunner:
    return default_runner()


def dump(result: ToolResult) -> str:
    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False)


def sqlite_dump(connection: sqlite3.Connection) -> str:
    return "\n".join(connection.iterdump())


def assert_no_secrets(text: str) -> None:
    for secret in SYNTHETIC_SECRETS:
        assert secret not in text
    assert HMAC_KEY.decode() not in text


# Path traversal e caminhos proibidos

BAD_PATHS = [
    "../outside-secret.txt",
    "src/../../outside-secret.txt",
    "/etc/passwd",
    "..",
    ".",
    "src//app.py",
    "src/./app.py",
    "src\\app.py",
    ".git/config",
    ".git/HEAD",
    ".GIT/config",
    "src/\x00app.py",
    ".env",
    ".env.production",
    "deploy/server.pem",
    "deploy/signing.key",
    "id_rsa",
    "credentials.json",
]

PATH_TOOLS: list[tuple[str, dict[str, Any]]] = [
    ("read_range", {"line_start": 1, "line_end": 2}),
    ("list_symbols", {}),
    ("git_blame", {"line_start": 1, "line_end": 2}),
    ("git_log", {}),
    ("git_show", {"sha": "a" * 40}),
    ("get_diff", {"ref": "HEAD..HEAD"}),
]


@pytest.mark.parametrize("bad_path", BAD_PATHS)
@pytest.mark.parametrize("tool, extra", PATH_TOOLS, ids=[t for t, _ in PATH_TOOLS])
def test_every_path_argument_rejects_traversal_and_sensitive_files(
    runner: ToolRunner,
    tool_ctx: ToolContext,
    tool: str,
    extra: dict[str, Any],
    bad_path: str,
) -> None:
    result = runner.call(tool_ctx, tool, {"path": bad_path, **extra})

    assert error_code(result) == "PATH_DENIED"
    assert "outside" not in dump(result)
    assert tool_ctx.evidence.all() == []


def test_symlink_to_a_file_outside_the_repository_is_denied(
    runner: ToolRunner, tool_ctx: ToolContext, fixture_repo: FixtureRepo
) -> None:
    result = runner.call(
        tool_ctx,
        "read_range",
        {"path": "link-out.txt", "line_start": 1, "line_end": 1},
    )

    assert error_code(result) == "PATH_DENIED"
    assert fixture_repo.outside_file.read_text().strip() not in dump(result)


def test_symlinked_file_is_not_indexed_or_searchable(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = runner.call(tool_ctx, "search_code", {"query": "conteudo fora"})

    assert isinstance(result.data, dict)
    assert result.data["items"] == []


def test_dotenv_is_denied_but_does_not_leak_through_the_other_tools(
    runner: ToolRunner, tool_ctx: ToolContext, fixture_repo: FixtureRepo
) -> None:
    ref = f"{fixture_repo.first}..{fixture_repo.head}"
    calls = [
        ("search_code", {"query": "DB_PASSWORD"}),
        ("changed_files", {"ref": ref}),
        ("get_diff", {"ref": ref}),
        ("git_show", {"sha": fixture_repo.commits[1]}),
    ]

    for name, args in calls:
        result = runner.call(tool_ctx, name, args)
        assert_no_secrets(dump(result))


def test_large_and_binary_files_are_denied_by_read_range(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    for path in ("big.txt", "blob.dat"):
        result = runner.call(
            tool_ctx,
            "read_range",
            {"path": path, "line_start": 1, "line_end": 1},
        )
        assert error_code(result) == "PATH_DENIED", path


def test_diff_tools_do_not_bypass_the_file_limits(
    runner: ToolRunner, tool_ctx: ToolContext, fixture_repo: FixtureRepo
) -> None:
    ref = f"{fixture_repo.first}..{fixture_repo.head}"

    whole = runner.call(tool_ctx, "get_diff", {"ref": ref})
    explicit = runner.call(tool_ctx, "get_diff", {"ref": ref, "path": "big.txt"})
    shown = runner.call(
        tool_ctx, "git_show", {"sha": fixture_repo.commits[1], "path": "blob.dat"}
    )

    assert isinstance(whole.data, dict)
    assert "AAAAAAAAAA" not in whole.data["diff"]
    assert "[omitted:" in whole.data["diff"]
    assert error_code(explicit) == "PATH_DENIED"
    assert error_code(shown) == "PATH_DENIED"


def test_no_tool_returns_a_whole_oversized_line(
    runner: ToolRunner, tool_ctx: ToolContext, fixture_repo: FixtureRepo
) -> None:
    (fixture_repo.root / "long.txt").write_text("x" * 5000 + "\n")
    repo = Repo(fixture_repo.root)
    author = Actor("Fixture Author", "fixture@example.com")
    repo.index.add(["long.txt"])
    tool_ctx.commit = repo.index.commit(
        "Add long line", author=author, committer=author
    ).hexsha

    result = runner.call(
        tool_ctx, "read_range", {"path": "long.txt", "line_start": 1, "line_end": 1}
    )

    assert result.ok
    assert len(dump(result)) <= tool_ctx.max_output_chars


# Redação: saídas, evidências e bancos


def battery(fixture_repo: FixtureRepo) -> list[tuple[str, dict[str, Any]]]:
    ref = f"{fixture_repo.first}..{fixture_repo.head}"
    return [
        ("search_code", {"query": "AWS_ACCESS_KEY"}),
        ("search_code", {"query": "postgres"}),
        ("search_code", {"query": "BEGIN RSA PRIVATE KEY"}),
        ("read_range", {"path": "src/app.py", "line_start": 1, "line_end": 40}),
        ("read_range", {"path": "src/app.py", "line_start": 25, "line_end": 28}),
        ("find_symbol", {"name": "calculate_total"}),
        ("find_references", {"symbol": "normalize_total"}),
        ("list_symbols", {"path": "src/app.py"}),
        ("git_log", {"limit": 5}),
        ("git_blame", {"path": "src/app.py", "line_start": 1, "line_end": 40}),
        ("git_show", {"sha": fixture_repo.commits[1]}),
        ("changed_files", {"ref": ref}),
        ("get_diff", {"ref": ref}),
        ("find_tests", {"target": "src/app.py"}),
        ("check_test_scenario", {"description": "sums prices", "target": "src/app.py"}),
    ]


def test_synthetic_secrets_never_reach_outputs_evidence_or_databases(
    runner: ToolRunner, tool_ctx: ToolContext, fixture_repo: FixtureRepo
) -> None:
    for name, args in battery(fixture_repo):
        result = runner.call(tool_ctx, name, args)
        assert_no_secrets(dump(result))

    verify_all(tool_ctx.evidence, tool_ctx.source, HMAC_KEY)  # type: ignore[arg-type]

    stored = tool_ctx.evidence.all()
    assert stored
    assert_no_secrets(json.dumps([e.model_dump(mode="json") for e in stored]))
    assert_no_secrets(sqlite_dump(tool_ctx.evidence.connection))
    assert_no_secrets(sqlite_dump(tool_ctx.cache.connection))


def test_the_battery_actually_touches_the_secrets(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = runner.call(
        tool_ctx, "read_range", {"path": "src/app.py", "line_start": 5, "line_end": 8}
    )

    assert "[REDACTED:aws_access_key]" in dump(result)
    assert "[REDACTED:github_token]" in dump(result)
    assert "[REDACTED:url_credentials]" in dump(result)


def test_private_key_body_is_redacted_even_when_the_range_starts_inside_it(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    lines = tool_ctx.file_view("src/app.py").lines
    begin = next(i for i, line in enumerate(lines) if "BEGIN RSA PRIVATE KEY" in line)

    result = runner.call(
        tool_ctx,
        "read_range",
        {"path": "src/app.py", "line_start": begin + 2, "line_end": begin + 3},
    )

    assert_no_secrets(dump(result))
    assert result.ok


def test_evidence_hash_is_over_the_original_text_not_the_redacted_one(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    runner.call(
        tool_ctx, "read_range", {"path": "src/app.py", "line_start": 5, "line_end": 5}
    )

    verify_all(tool_ctx.evidence, tool_ctx.source, HMAC_KEY)  # type: ignore[arg-type]

    (evidence,) = tool_ctx.evidence.all()
    assert evidence.verify_status.value == "verified"
    assert "AKIA" not in evidence.excerpt


# Conteúdo hostil é dado


def test_injection_in_code_and_in_commit_message_is_returned_only_as_data(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    found = runner.call(tool_ctx, "search_code", {"query": "previous instructions"})
    history = runner.call(tool_ctx, "git_log", {"limit": 1})

    assert isinstance(found.data, dict) and isinstance(history.data, dict)
    assert any("IGNORE ALL PREVIOUS" in i["snippet"] for i in found.data["items"])
    assert "Ignore previous instructions" in history.data["items"][0]["assunto"]
    # nada além do que foi pedido foi executado
    assert tool_ctx.usage.tool_calls == 2


# Hooks e configuração maliciosos do repositório analisado


def make_hostile(fixture_repo: FixtureRepo, tmp_path: Path) -> Path:
    marker_dir = tmp_path / "markers"
    marker_dir.mkdir()

    def script(name: str) -> Path:
        path = tmp_path / f"{name}.sh"
        path.write_text(
            f'#!/bin/sh\ntouch "{marker_dir}/{name}"\ncat "$1" 2>/dev/null\n'
        )
        make_executable(path)
        return path

    for hook in ("post-checkout", "pre-commit", "post-merge", "reference-transaction"):
        hook_path = fixture_repo.root / ".git" / "hooks" / hook
        hook_path.write_text(f'#!/bin/sh\ntouch "{marker_dir}/hook-{hook}"\n')
        make_executable(hook_path)

    with (fixture_repo.root / ".git" / "config").open("a", encoding="utf-8") as config:
        config.write(
            "\n[core]\n"
            f"\tfsmonitor = {script('fsmonitor')}\n"
            f"\tpager = {script('pager')}\n"
            f"\thooksPath = {fixture_repo.root / '.git' / 'hooks'}\n"
            "[diff]\n"
            f"\texternal = {script('external-diff')}\n"
            '[diff "evil"]\n'
            f"\ttextconv = {script('textconv')}\n"
            '[filter "evil"]\n'
            f"\tsmudge = {script('smudge')}\n"
            f"\tclean = {script('clean')}\n"
        )

    repo = Repo(fixture_repo.root)
    (fixture_repo.root / ".gitattributes").write_text(
        "*.py diff=evil filter=evil\n*.js diff=evil\n", encoding="utf-8"
    )
    author = Actor("Fixture Author", "fixture@example.com")
    repo.index.add([".gitattributes"])
    repo.index.commit(
        "Add hostile attributes", author=author, committer=author, skip_hooks=True
    )

    return marker_dir


def test_hostile_hooks_and_git_config_are_never_executed(
    runner: ToolRunner, fixture_repo: FixtureRepo, tmp_path: Path
) -> None:
    marker_dir = make_hostile(fixture_repo, tmp_path)
    source = LocalRepoSource(fixture_repo.root, [fixture_repo.allowed_root])
    head = source.resolve_commit()

    ctx = ToolContext(
        repo="fixture-repo",
        commit=head,
        source=source,
        index=IndexStore(tmp_path / "index.sqlite"),
        evidence=EvidenceStore("analysis-hostile"),
        cache=ToolCache(),
        hmac_key=HMAC_KEY,
    )
    ref = f"{fixture_repo.first}..{head}"

    calls: list[tuple[str, dict[str, Any]]] = [
        ("search_code", {"query": "calculate_total"}),
        ("read_range", {"path": "src/app.py", "line_start": 1, "line_end": 5}),
        ("find_symbol", {"name": "calculate_total"}),
        ("git_log", {"path": "src/app.py"}),
        ("git_blame", {"path": "src/app.py", "line_start": 1, "line_end": 10}),
        ("git_show", {"sha": fixture_repo.commits[2]}),
        ("changed_files", {"ref": ref}),
        ("get_diff", {"ref": ref}),
        ("get_diff", {"ref": ref, "path": "src/utils.py"}),
        ("find_tests", {"target": "src/utils.py"}),
    ]

    for name, args in calls:
        result = runner.call(ctx, name, args)
        assert result.ok, (name, result.error)

    assert sorted(path.name for path in marker_dir.iterdir()) == []

    ctx.index.close()
    ctx.evidence.close()
    ctx.cache.close()

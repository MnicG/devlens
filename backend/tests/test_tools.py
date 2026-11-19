import json
from typing import Any

import pytest
from app.evidence.verify import verify_all
from app.schemas.contracts import EvidenceKind, ToolResult, VerifyStatus
from app.tools.base import (
    TOOL_FAILURE,
    PagedArgs,
    Tool,
    ToolContext,
    ToolRunner,
    error_code,
)
from app.tools.registry import default_runner
from conftest import HMAC_KEY, FixtureRepo


@pytest.fixture
def runner() -> ToolRunner:
    return default_runner()


def call(
    runner: ToolRunner, ctx: ToolContext, tool_name: str, /, **args: Any
) -> ToolResult:
    return runner.call(ctx, tool_name, args)


def data_of(result: ToolResult) -> dict[str, Any]:
    assert result.ok, result.error
    assert isinstance(result.data, dict)
    return result.data


# search_code


def test_search_code_returns_hits_with_registered_evidence(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = call(runner, tool_ctx, "search_code", query="calculate_total")
    data = data_of(result)

    paths = {item["path"] for item in data["items"]}
    assert "src/app.py" in paths
    assert all(item["ev_id"].startswith("ev_") for item in data["items"])
    assert set(data["evidence_ids"]) == {i["ev_id"] for i in data["items"]}
    assert "_cand" not in json.dumps(data)
    assert result.candidates == []

    stored = tool_ctx.evidence.all()
    assert {e.kind for e in stored} == {EvidenceKind.CODE}
    assert all(e.verify_status is VerifyStatus.PENDING for e in stored)


def test_search_code_snippet_shows_the_matching_line(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = call(runner, tool_ctx, "search_code", query="previous instructions")
    (item,) = [i for i in data_of(result)["items"] if i["path"] == "src/app.py"]

    assert "IGNORE ALL PREVIOUS" in item["snippet"]
    assert item["line_start"] > 1


def test_search_code_builds_the_index_on_demand(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    before = tool_ctx.index.connection.execute(
        "SELECT COUNT(*) FROM indexed_files"
    ).fetchone()[0]

    result = call(runner, tool_ctx, "search_code", query="normalize_total")

    assert before == 0
    assert result.ok
    assert tool_ctx.last_call is not None and tool_ctx.last_call.counted


def test_index_missing_does_not_count_in_the_budget(
    runner: ToolRunner,
    tool_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tool_ctx, "build_index", lambda: None)

    result = call(runner, tool_ctx, "search_code", query="calculate_total")

    assert error_code(result) == "INDEX_MISSING"
    assert tool_ctx.usage.tool_calls == 0


def test_search_code_snippets_respect_the_limits(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = call(runner, tool_ctx, "search_code", query="def", top_k=20)

    for item in data_of(result)["items"]:
        assert len(item["snippet"]) <= 240
        assert item["line_end"] - item["line_start"] <= 2


def test_search_code_honors_path_glob(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = call(runner, tool_ctx, "search_code", query="total", path_glob="tests/*")

    assert {i["path"] for i in data_of(result)["items"]} <= {
        "tests/test_app.py",
        "tests/test_utils.py",
    }


# read_range


def test_read_range_returns_numbered_lines_and_one_code_evidence(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = call(
        runner, tool_ctx, "read_range", path="src/utils.py", line_start=1, line_end=3
    )
    data = data_of(result)

    assert [row["n"] for row in data["lines"]] == [1, 2, 3]
    assert data["lines"][0]["text"] == "def normalize_total(value):"
    (evidence,) = tool_ctx.evidence.all()
    assert (evidence.path, evidence.line_start, evidence.line_end) == (
        "src/utils.py",
        1,
        3,
    )
    assert data["evidence_ids"] == [evidence.id]


def test_read_range_evidence_verifies_against_the_commit(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    call(runner, tool_ctx, "read_range", path="src/utils.py", line_start=1, line_end=3)

    verify_all(tool_ctx.evidence, tool_ctx.source, HMAC_KEY)  # type: ignore[arg-type]

    assert all(
        e.verify_status is VerifyStatus.VERIFIED for e in tool_ctx.evidence.all()
    )


def test_read_range_clamps_the_end_to_the_file_length(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = call(
        runner, tool_ctx, "read_range", path="README.md", line_start=1, line_end=50
    )

    assert data_of(result)["line_end"] == 1


@pytest.mark.parametrize(
    "args",
    [
        {"path": "src/utils.py", "line_start": 1, "line_end": 122},
        {"path": "src/utils.py", "line_start": 5, "line_end": 2},
        {"path": "src/utils.py", "line_start": 99, "line_end": 100},
        {"path": "src/utils.py", "line_start": 0, "line_end": 3},
        {"path": "src/utils.py", "line_start": 1},
        {"path": "src/utils.py", "line_start": 1, "line_end": 3, "extra": 1},
    ],
)
def test_read_range_rejects_invalid_arguments(
    runner: ToolRunner, tool_ctx: ToolContext, args: dict[str, Any]
) -> None:
    assert error_code(runner.call(tool_ctx, "read_range", args)) == "INVALID_ARGS"


def test_read_range_truncates_by_characters_and_offers_a_cursor(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    tool_ctx.max_output_chars = 1200

    result = call(
        runner, tool_ctx, "read_range", path="src/app.py", line_start=1, line_end=40
    )
    data = data_of(result)

    assert result.truncated
    assert len(json.dumps(data)) <= 1200 + 600
    assert data["cursor"] == str(data["line_end"] + 1)
    (evidence,) = tool_ctx.evidence.all()
    assert evidence.line_end == data["line_end"]


def test_read_range_missing_file_is_not_found(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = call(
        runner, tool_ctx, "read_range", path="src/none.py", line_start=1, line_end=2
    )

    assert error_code(result) == "NOT_FOUND"


# símbolos


def test_find_symbol_returns_definitions_with_evidence(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = call(
        runner, tool_ctx, "find_symbol", name="calculate_total", kind="function"
    )
    (item,) = data_of(result)["items"]

    assert item["path"] == "src/app.py"
    assert item["kind"] == "function"
    assert item["ev_id"]


def test_find_symbol_filters_by_kind_and_reports_unknown_names_as_empty(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    assert data_of(call(runner, tool_ctx, "find_symbol", name="nope"))["items"] == []
    assert (
        data_of(
            call(runner, tool_ctx, "find_symbol", name="calculate_total", kind="class")
        )["items"]
        == []
    )


def test_list_symbols_has_no_evidence(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = call(runner, tool_ctx, "list_symbols", path="src/utils.py")
    names = {item["name"] for item in data_of(result)["items"]}

    assert {"normalize_total", "unused_helper"} <= names
    assert tool_ctx.evidence.all() == []


def test_find_references_is_marked_approximate(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = call(runner, tool_ctx, "find_references", symbol="normalize_total")
    data = data_of(result)

    assert data["precision"] == "approximate"
    assert {item["path"] for item in data["items"]} >= {"src/app.py"}
    assert all(item["ev_id"] for item in data["items"])


# limites, cursor e erros


@pytest.mark.parametrize(
    "name, args",
    [
        ("search_code", {"query": "x", "top_k": 21}),
        ("search_code", {"query": "x", "top_k": 0}),
        ("search_code", {"query": "x" * 201}),
        ("search_code", {"query": ""}),
        ("find_references", {"symbol": "x", "top_k": 21}),
        ("git_log", {"limit": 21}),
        ("git_show", {"sha": "zzzz"}),
        ("git_show", {"sha": "abc"}),
        ("git_show", {"sha": "--output=/tmp/x"}),
        ("git_show", {"sha": "A" * 40}),
        ("git_show", {"sha": "a" * 41}),
        ("related_issues", {"query": "x", "top_k": 11}),
        ("no_such_tool", {}),
    ],
)
def test_arguments_outside_the_limits_are_invalid(
    runner: ToolRunner, tool_ctx: ToolContext, name: str, args: dict[str, Any]
) -> None:
    result = runner.call(tool_ctx, name, args)

    assert error_code(result) == "INVALID_ARGS"
    assert tool_ctx.usage.tool_calls == 1


def test_validation_errors_do_not_echo_the_input(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = runner.call(tool_ctx, "search_code", {"query": "SECRET-" + "x" * 300})

    assert result.error is not None
    assert "SECRET-" not in result.error


def test_output_is_truncated_with_a_cursor_and_pages_are_disjoint(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    tool_ctx.max_output_chars = 450
    first = call(runner, tool_ctx, "search_code", query="def", top_k=20)
    first_data = data_of(first)

    assert first.truncated
    assert len(json.dumps(first_data)) <= 450
    cursor = first_data["cursor"]

    seen = [(i["path"], i["line_start"]) for i in first_data["items"]]
    while cursor is not None:
        page = call(
            runner, tool_ctx, "search_code", query="def", top_k=20, cursor=cursor
        )
        page_data = data_of(page)
        seen += [(i["path"], i["line_start"]) for i in page_data["items"]]
        cursor = page_data.get("cursor")

    tool_ctx.max_output_chars = 100_000
    full = data_of(call(runner, tool_ctx, "search_code", query="def", top_k=20))
    assert seen == [(i["path"], i["line_start"]) for i in full["items"]]
    assert len(seen) == len(set(seen))


def test_only_returned_items_register_evidence(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    tool_ctx.max_output_chars = 450

    data = data_of(call(runner, tool_ctx, "search_code", query="def", top_k=20))

    assert len(tool_ctx.evidence.all()) == len(data["items"])


@pytest.mark.parametrize("cursor", ["abc", "-1", "9999", "1.5"])
def test_invalid_cursor_is_rejected(
    runner: ToolRunner, tool_ctx: ToolContext, cursor: str
) -> None:
    result = call(runner, tool_ctx, "search_code", query="def", cursor=cursor)

    assert error_code(result) == "INVALID_ARGS"


# cache e orçamento


def test_cache_hit_does_not_count_and_returns_the_same_data(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    first = call(runner, tool_ctx, "find_symbol", name="calculate_total")
    calls_after_first = tool_ctx.usage.tool_calls

    second = call(runner, tool_ctx, "find_symbol", name="calculate_total")

    assert calls_after_first == 1
    assert tool_ctx.usage.tool_calls == 1
    assert tool_ctx.last_call is not None and tool_ctx.last_call.cache_hit
    assert second.data == first.data


def test_cache_is_keyed_by_arguments_and_commit(
    runner: ToolRunner, tool_ctx: ToolContext, fixture_repo: FixtureRepo
) -> None:
    call(runner, tool_ctx, "read_range", path="src/utils.py", line_start=1, line_end=2)
    call(runner, tool_ctx, "read_range", path="src/utils.py", line_start=1, line_end=3)
    assert tool_ctx.usage.tool_calls == 2

    tool_ctx.commit = fixture_repo.first
    call(runner, tool_ctx, "read_range", path="src/utils.py", line_start=1, line_end=2)
    assert tool_ctx.usage.tool_calls == 3


def test_cache_does_not_hide_the_cursor(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    tool_ctx.max_output_chars = 450
    first = data_of(call(runner, tool_ctx, "search_code", query="def", top_k=20))

    second = data_of(
        call(
            runner,
            tool_ctx,
            "search_code",
            query="def",
            top_k=20,
            cursor=first["cursor"],
        )
    )

    assert second["items"] != first["items"]
    assert tool_ctx.usage.tool_calls == 1


def test_cached_results_still_register_evidence_for_the_analysis(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    first = data_of(call(runner, tool_ctx, "find_symbol", name="calculate_total"))
    tool_ctx.evidence.connection.execute("DELETE FROM evidences")

    call(runner, tool_ctx, "find_symbol", name="calculate_total")

    assert len(tool_ctx.evidence.all()) == len(first["items"])


def test_budget_exhaustion_blocks_new_calls_but_not_cache_hits(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    tool_ctx.budget.max_tool_calls = 1
    call(runner, tool_ctx, "find_symbol", name="calculate_total")

    blocked = call(runner, tool_ctx, "find_symbol", name="unused_helper")
    cached = call(runner, tool_ctx, "find_symbol", name="calculate_total")

    assert error_code(blocked) == "BUDGET_EXCEEDED"
    assert cached.ok
    assert tool_ctx.usage.tool_calls == 1


def test_source_unavailable_does_not_count(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = call(runner, tool_ctx, "related_issues", query="rounding")

    assert error_code(result) == "SOURCE_UNAVAILABLE"
    assert tool_ctx.usage.tool_calls == 0


def test_not_found_and_path_denied_count(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    call(runner, tool_ctx, "read_range", path="src/none.py", line_start=1, line_end=2)
    call(runner, tool_ctx, "read_range", path=".env", line_start=1, line_end=2)

    assert tool_ctx.usage.tool_calls == 2


class _Exploding(Tool):
    name = "explode"
    args_schema = PagedArgs

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        raise RuntimeError("/home/operator/secret/path blew up")


def test_unexpected_failures_become_errors_without_internal_details(
    tool_ctx: ToolContext,
) -> None:
    result = ToolRunner([_Exploding()]).call(tool_ctx, "explode", {})

    assert error_code(result) == TOOL_FAILURE
    assert result.error is not None and "operator" not in result.error


# histórico


def test_git_log_lists_commits_newest_first(
    runner: ToolRunner, tool_ctx: ToolContext, fixture_repo: FixtureRepo
) -> None:
    data = data_of(call(runner, tool_ctx, "git_log", limit=2))

    assert [i["sha"] for i in data["items"]] == fixture_repo.commits[::-1][:2]
    assert {e.kind for e in tool_ctx.evidence.all()} == {EvidenceKind.COMMIT}


def test_git_log_filters_by_path_and_since(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    by_path = data_of(call(runner, tool_ctx, "git_log", path="src/utils.py"))
    future = data_of(call(runner, tool_ctx, "git_log", since="2999-01-01"))

    assert len(by_path["items"]) == 2
    assert future["items"] == []
    assert (
        error_code(call(runner, tool_ctx, "git_log", since="ontem")) == "INVALID_ARGS"
    )


def test_git_blame_uses_redacted_content_and_registers_commits(
    runner: ToolRunner, tool_ctx: ToolContext, fixture_repo: FixtureRepo
) -> None:
    data = data_of(
        call(runner, tool_ctx, "git_blame", path="src/app.py", line_start=3, line_end=7)
    )

    assert len(data["items"]) == 5
    assert {i["sha"] for i in data["items"]} == {fixture_repo.commits[1]}
    assert "AKIA" not in json.dumps(data)
    assert len(data["evidence_ids"]) == 1


def test_git_blame_rejects_more_than_sixty_lines(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = call(
        runner, tool_ctx, "git_blame", path="src/app.py", line_start=1, line_end=62
    )

    assert error_code(result) == "INVALID_ARGS"


def test_git_show_returns_metadata_and_a_bounded_diff(
    runner: ToolRunner, tool_ctx: ToolContext, fixture_repo: FixtureRepo
) -> None:
    data = data_of(call(runner, tool_ctx, "git_show", sha=fixture_repo.head[:10]))

    assert data["sha"] == fixture_repo.head
    assert "round(value, 3)" in data["diff"]
    assert len(json.dumps(data)) <= 4000
    assert len(tool_ctx.evidence.all()) == 1


def test_git_show_of_the_root_commit_has_no_diff(
    runner: ToolRunner, tool_ctx: ToolContext, fixture_repo: FixtureRepo
) -> None:
    data = data_of(call(runner, tool_ctx, "git_show", sha=fixture_repo.first))

    assert data["diff"] == ""


def test_git_show_unknown_commit_is_not_found(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    assert error_code(call(runner, tool_ctx, "git_show", sha="f" * 40)) == "NOT_FOUND"


def test_git_show_truncates_long_diffs(
    runner: ToolRunner, tool_ctx: ToolContext, fixture_repo: FixtureRepo
) -> None:
    tool_ctx.max_output_chars = 800

    result = call(runner, tool_ctx, "git_show", sha=fixture_repo.commits[1])

    assert result.truncated
    assert len(json.dumps(data_of(result))) <= 800 + 400


def test_changed_files_lists_status_and_counts(
    runner: ToolRunner, tool_ctx: ToolContext, fixture_repo: FixtureRepo
) -> None:
    ref = f"{fixture_repo.first}..{fixture_repo.head}"

    items = data_of(call(runner, tool_ctx, "changed_files", ref=ref))["items"]
    by_path = {i["path"]: i for i in items}

    assert by_path["src/app.py"]["status"] == "added"
    assert by_path["src/app.py"]["additions"] > 10
    assert by_path["src/utils.py"] == {
        "path": "src/utils.py",
        "status": "modified",
        "additions": 1,
        "deletions": 1,
    }


def test_changed_files_rejects_malformed_and_unknown_refs(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    assert (
        error_code(call(runner, tool_ctx, "changed_files", ref="main"))
        == "INVALID_ARGS"
    )
    assert (
        error_code(call(runner, tool_ctx, "changed_files", ref=f"{'f' * 40}..HEAD"))
        == "NOT_FOUND"
    )
    assert (
        error_code(call(runner, tool_ctx, "changed_files", ref="-x..HEAD"))
        == "NOT_FOUND"
    )


def read_all_diff(
    runner: ToolRunner, ctx: ToolContext, ref: str, limit: int
) -> list[str]:
    ctx.max_output_chars = limit
    pages: list[str] = []
    cursor = None

    while True:
        args: dict[str, Any] = {"ref": ref}
        if cursor:
            args["cursor"] = cursor
        data = data_of(runner.call(ctx, "get_diff", args))
        assert len(json.dumps(data)) <= limit
        pages.append(data["diff"])
        cursor = data.get("cursor")
        if not cursor:
            return pages


def test_get_diff_pages_with_a_cursor(
    runner: ToolRunner, tool_ctx: ToolContext, fixture_repo: FixtureRepo
) -> None:
    ref = f"{fixture_repo.first}..{fixture_repo.head}"

    small = read_all_diff(runner, tool_ctx, ref, 900)
    large = read_all_diff(runner, tool_ctx, ref, 100_000)

    assert len(small) > len(large) >= 1
    assert "\n".join(small) == "\n".join(large)


def test_get_diff_for_one_path_and_bad_cursor(
    runner: ToolRunner, tool_ctx: ToolContext, fixture_repo: FixtureRepo
) -> None:
    ref = f"{fixture_repo.first}..{fixture_repo.head}"

    only = data_of(call(runner, tool_ctx, "get_diff", ref=ref, path="src/utils.py"))

    assert "src/app.py" not in only["diff"]
    assert error_code(call(runner, tool_ctx, "get_diff", ref=ref, cursor="99999")) == (
        "INVALID_ARGS"
    )


# testes relacionados


def test_find_tests_by_file_name_convention(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = call(runner, tool_ctx, "find_tests", target="src/utils.py")
    names = {i["name"] for i in data_of(result)["items"]}

    assert {
        "test_normalize_total_rounds_two_decimals",
        "test_normalize_total_keeps_integers",
    } <= names
    assert {e.kind for e in tool_ctx.evidence.all()} == {EvidenceKind.TEST}


def test_find_tests_by_symbol_through_imports_and_calls(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = call(runner, tool_ctx, "find_tests", target="calculate_total")

    assert {i["path"] for i in data_of(result)["items"]} == {"tests/test_app.py"}


def test_find_tests_understands_javascript_tests(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = call(runner, tool_ctx, "find_tests", target="web/web.js")
    (item,) = data_of(result)["items"]

    assert item["name"] == "formats price with two decimals"
    assert item["path"] == "web/web.test.js"


def test_find_tests_without_related_tests_is_empty(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    result = call(runner, tool_ctx, "find_tests", target="unused_helper")

    assert data_of(result)["items"] == []


def test_test_evidence_verifies(runner: ToolRunner, tool_ctx: ToolContext) -> None:
    call(runner, tool_ctx, "find_tests", target="src/utils.py")

    verify_all(tool_ctx.evidence, tool_ctx.source, HMAC_KEY)  # type: ignore[arg-type]

    assert all(
        e.verify_status is VerifyStatus.VERIFIED for e in tool_ctx.evidence.all()
    )


def test_check_test_scenario_true_when_a_test_matches_the_scenario(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    data = data_of(
        call(
            runner,
            tool_ctx,
            "check_test_scenario",
            description="normalize total rounds to two decimals",
            target="src/utils.py",
        )
    )

    assert data["covered"] is True
    assert data["items"][0]["name"] == "test_normalize_total_rounds_two_decimals"


def test_check_test_scenario_is_undecided_instead_of_guessing(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    data = data_of(
        call(
            runner,
            tool_ctx,
            "check_test_scenario",
            description="handles negative numbers with currency conversion",
            target="src/utils.py",
        )
    )

    assert data["covered"] is None


def test_check_test_scenario_false_only_without_any_related_test(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    data = data_of(
        call(
            runner,
            tool_ctx,
            "check_test_scenario",
            description="helper returns nothing",
            target="unused_helper",
        )
    )

    assert data == {"covered": False, "items": []}


def test_read_range_rejects_more_than_120_lines_per_call(
    runner: ToolRunner, tool_ctx: ToolContext
) -> None:
    too_wide = call(
        runner, tool_ctx, "read_range", path="src/app.py", line_start=1, line_end=122
    )
    assert error_code(too_wide) == "INVALID_ARGS"

    limit = call(
        runner, tool_ctx, "read_range", path="src/app.py", line_start=1, line_end=120
    )
    assert limit.ok


def test_changed_files_never_lists_more_than_100_files_across_pages(
    runner: ToolRunner, tool_ctx: ToolContext, fixture_repo: FixtureRepo
) -> None:
    from conftest import AUTHOR
    from git import Repo

    repo = Repo(fixture_repo.root)
    before = fixture_repo.head
    names = [f"bulk_{number:03d}.txt" for number in range(105)]
    for name in names:
        (fixture_repo.root / name).write_text("x\n")
    repo.index.add(names)
    after = repo.index.commit("bulk", author=AUTHOR, committer=AUTHOR).hexsha
    ref = f"{before}..{after}"

    first = call(runner, tool_ctx, "changed_files", ref=ref)
    assert first.truncated

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        page = data_of(call(runner, tool_ctx, "changed_files", ref=ref, cursor=cursor))
        seen += [item["path"] for item in page["items"]]
        cursor = page.get("cursor")
        if cursor is None:
            break

    assert cursor is None
    assert len(seen) == len(set(seen)) == 100

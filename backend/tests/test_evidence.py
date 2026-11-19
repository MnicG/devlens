from pathlib import Path

import pytest
from app.evidence.candidates import code_candidate, commit_candidate
from app.evidence.confidence import score_finding
from app.evidence.hashing import (
    evidence_id,
    load_hmac_key,
    snippet_hmac,
)
from app.evidence.store import EvidenceStore
from app.evidence.verify import verify_all, verify_evidence
from app.schemas.contracts import (
    Category,
    Confidence,
    Evidence,
    EvidenceKind,
    Finding,
    FindingStatus,
    Severity,
    VerifyStatus,
)
from app.tools.repo_source import LocalRepoSource
from conftest import AWS_KEY, HMAC_KEY, FixtureRepo

# HMAC e identificadores


def test_hmac_depends_on_key_and_text() -> None:
    assert snippet_hmac(b"k" * 16, "x") == snippet_hmac(b"k" * 16, "x")
    assert snippet_hmac(b"k" * 16, "x") != snippet_hmac(b"j" * 16, "x")
    assert snippet_hmac(b"k" * 16, "x") != snippet_hmac(b"k" * 16, "y")


def test_load_hmac_key_requires_a_configured_key() -> None:
    with pytest.raises(RuntimeError, match="DEVLENS_HMAC_KEY"):
        load_hmac_key({})

    with pytest.raises(RuntimeError):
        load_hmac_key({"DEVLENS_HMAC_KEY": "short"})

    assert load_hmac_key({"DEVLENS_HMAC_KEY": "x" * 20}) == b"x" * 20


def test_evidence_id_is_deterministic_and_location_sensitive() -> None:
    args = ("repo", "c" * 40, "code", "a.py", 1, 3)

    assert evidence_id(*args) == evidence_id(*args)
    assert evidence_id(*args).startswith("ev_")
    assert evidence_id(*args) != evidence_id("repo", "c" * 40, "code", "a.py", 1, 4)
    assert evidence_id(*args) != evidence_id(*args, ref="x")


# Evidence Store


def make_candidate(**overrides: object) -> Evidence:
    values: dict[str, object] = {
        "repo": "repo",
        "commit": "c" * 40,
        "path": "a.py",
        "content": "one\ntwo\nthree\n",
        "line_start": 1,
        "line_end": 2,
        "hmac_key": HMAC_KEY,
    }
    values.update(overrides)
    return code_candidate(**values)  # type: ignore[arg-type]


def test_store_assigns_deterministic_ids_and_deduplicates() -> None:
    store = EvidenceStore("a1")

    first = store.register(make_candidate())
    second = store.register(make_candidate())

    assert first.id == second.id
    assert len(store.all()) == 1
    assert first.verify_status is VerifyStatus.PENDING


def test_store_keeps_analyses_separate(tmp_path: Path) -> None:
    database = tmp_path / "evidence.sqlite"
    one = EvidenceStore("a1", database)
    two = EvidenceStore("a2", database)

    registered = one.register(make_candidate())

    assert two.get(registered.id) is None
    assert two.unknown_ids([registered.id]) == [registered.id]


def test_store_reports_ids_that_no_tool_returned() -> None:
    store = EvidenceStore("a1")
    known = store.register(make_candidate())

    assert store.unknown_ids([known.id, "ev_invented"]) == ["ev_invented"]


def test_store_redacts_and_truncates_excerpt_on_register() -> None:
    store = EvidenceStore("a1")
    raw = make_candidate().model_copy(
        update={"excerpt": f'key = "{AWS_KEY}"' + " x" * 400}
    )

    stored = store.register(raw)

    assert AWS_KEY not in stored.excerpt
    assert len(stored.excerpt) <= 600


def test_candidate_excerpt_is_redacted_but_hash_covers_the_original() -> None:
    content = f'key = "{AWS_KEY}"\nother = 1\n'

    candidate = make_candidate(content=content, line_start=1, line_end=1)

    assert AWS_KEY not in candidate.excerpt
    assert candidate.snippet_hash == snippet_hmac(HMAC_KEY, f'key = "{AWS_KEY}"')


def test_candidate_rejects_line_range_outside_the_file() -> None:
    with pytest.raises(ValueError):
        make_candidate(line_start=2, line_end=99)


# Verificação


def code_evidence(
    source: LocalRepoSource, repo: FixtureRepo, **kwargs: int
) -> Evidence:
    path = "src/utils.py"
    content = source.read_file(repo.head, path)
    return code_candidate(
        repo="r",
        commit=repo.head,
        path=path,
        content=content,
        line_start=kwargs.get("line_start", 1),
        line_end=kwargs.get("line_end", 2),
        hmac_key=HMAC_KEY,
    )


def test_verifies_code_evidence_that_matches_the_commit(
    source: LocalRepoSource, fixture_repo: FixtureRepo
) -> None:
    result = verify_evidence(code_evidence(source, fixture_repo), source, HMAC_KEY)

    assert result.verify_status is VerifyStatus.VERIFIED


def test_fails_when_hash_does_not_match(
    source: LocalRepoSource, fixture_repo: FixtureRepo
) -> None:
    evidence = code_evidence(source, fixture_repo).model_copy(
        update={"snippet_hash": "0" * 64}
    )

    result = verify_evidence(evidence, source, HMAC_KEY)

    assert result.verify_status is VerifyStatus.FAILED
    assert "hash" in result.verify_note


def test_fails_with_a_different_key(
    source: LocalRepoSource, fixture_repo: FixtureRepo
) -> None:
    evidence = code_evidence(source, fixture_repo)

    result = verify_evidence(evidence, source, b"another-key-0123456789")

    assert result.verify_status is VerifyStatus.FAILED


def test_fails_when_the_snippet_is_from_another_commit(
    source: LocalRepoSource, fixture_repo: FixtureRepo
) -> None:
    # utils.py mudou no último commit: o trecho do HEAD não existe no primeiro.
    evidence = code_evidence(source, fixture_repo).model_copy(
        update={"commit": fixture_repo.first}
    )

    assert (
        verify_evidence(evidence, source, HMAC_KEY).verify_status is VerifyStatus.FAILED
    )


@pytest.mark.parametrize(
    "update, expected",
    [
        ({"path": "src/missing.py"}, "does not exist"),
        ({"path": ".env"}, "cannot be read"),
        ({"path": "../outside-secret.txt"}, "cannot be read"),
        ({"path": "link-out.txt"}, "cannot be read"),
        ({"path": "blob.dat"}, "as text"),
        ({"line_start": 50, "line_end": 60}, "outside the file"),
        ({"line_start": 3, "line_end": 2}, "outside the file"),
        ({"line_start": 0}, "outside the file"),
        ({"path": None}, "no path"),
        ({"snippet_hash": None}, "no snippet hash"),
        ({"commit": "not-a-sha"}, "invalid commit"),
    ],
)
def test_fails_for_invalid_code_evidence(
    source: LocalRepoSource,
    fixture_repo: FixtureRepo,
    update: dict[str, object],
    expected: str,
) -> None:
    evidence = code_evidence(source, fixture_repo).model_copy(update=update)

    result = verify_evidence(evidence, source, HMAC_KEY)

    assert result.verify_status is VerifyStatus.FAILED
    assert expected in result.verify_note


def test_verifies_commit_evidence(
    source: LocalRepoSource, fixture_repo: FixtureRepo
) -> None:
    good = commit_candidate(
        repo="r", commit=fixture_repo.head, sha=fixture_repo.first, summary="x"
    )
    short = good.model_copy(update={"ref": fixture_repo.first[:8]})
    missing = good.model_copy(update={"ref": "f" * 40})
    branch = good.model_copy(update={"ref": "HEAD"})

    assert (
        verify_evidence(good, source, HMAC_KEY).verify_status is VerifyStatus.VERIFIED
    )
    assert (
        verify_evidence(short, source, HMAC_KEY).verify_status is VerifyStatus.VERIFIED
    )
    assert (
        verify_evidence(missing, source, HMAC_KEY).verify_status is VerifyStatus.FAILED
    )
    assert (
        verify_evidence(branch, source, HMAC_KEY).verify_status is VerifyStatus.FAILED
    )


def test_issue_evidence_fails_when_the_source_cannot_check_issues(
    source: LocalRepoSource, fixture_repo: FixtureRepo
) -> None:
    issue = Evidence(
        id="",
        repo="r",
        commit=fixture_repo.head,
        kind=EvidenceKind.ISSUE,
        ref="12",
    )

    result = verify_evidence(issue, source, HMAC_KEY)

    assert result.verify_status is VerifyStatus.FAILED
    assert "issues" in result.verify_note


def test_verify_all_persists_the_result(
    source: LocalRepoSource, fixture_repo: FixtureRepo
) -> None:
    store = EvidenceStore("a1")
    good = store.register(code_evidence(source, fixture_repo))
    bad = store.register(
        code_evidence(source, fixture_repo, line_start=1, line_end=3).model_copy(
            update={"snippet_hash": "0" * 64}
        )
    )

    verify_all(store, source, HMAC_KEY)

    stored_good = store.get(good.id)
    stored_bad = store.get(bad.id)
    assert stored_good is not None and stored_bad is not None
    assert stored_good.verify_status is VerifyStatus.VERIFIED
    assert stored_bad.verify_status is VerifyStatus.FAILED


# Regra de confiança


def ev(
    ident: str,
    kind: EvidenceKind,
    status: VerifyStatus = VerifyStatus.VERIFIED,
    **kwargs: object,
) -> Evidence:
    values: dict[str, object] = {
        "id": ident,
        "repo": "r",
        "commit": "c" * 40,
        "kind": kind,
        "verify_status": status,
    }
    if kind in {EvidenceKind.CODE, EvidenceKind.TEST}:
        values.update(path="a.py", line_start=10, line_end=20)
    if kind is EvidenceKind.COMMIT:
        values.update(ref="abc1234def")
    if kind is EvidenceKind.ISSUE:
        values.update(ref="7")
    values.update(kwargs)
    return Evidence(**values)  # type: ignore[arg-type]


def finding(ids: list[str], contradicting: list[str] | None = None) -> Finding:
    return Finding(
        id="f1",
        category=Category.ROOT_CAUSE,
        severity=Severity.MEDIUM,
        title="t",
        summary="s",
        evidence_ids=ids,
        contradicting_evidence_ids=contradicting or [],
    )


def score(
    ids: list[str], evidences: list[Evidence], contradicting: list[str] | None = None
) -> Finding:
    return score_finding(finding(ids, contradicting), {e.id: e for e in evidences})


def test_no_verified_evidence_discards_the_finding() -> None:
    result = score(["e1"], [ev("e1", EvidenceKind.CODE, VerifyStatus.FAILED)])

    assert result.status is FindingStatus.DISCARDED
    assert result.confidence is None


def test_unknown_evidence_ids_discard_the_finding() -> None:
    result = score(["ev_invented"], [])

    assert result.status is FindingStatus.DISCARDED


def test_high_needs_two_verified_of_distinct_kinds_with_a_strong_one() -> None:
    result = score(
        ["e1", "e2"],
        [ev("e1", EvidenceKind.CODE), ev("e2", EvidenceKind.COMMIT)],
    )

    assert result.confidence is Confidence.HIGH
    assert result.status is FindingStatus.KEPT
    assert result.confidence_rationale == (
        "2 evidências verificadas: código em `a.py:10-20` e commit `abc1234`."
    )


def test_two_indirect_kinds_are_not_high() -> None:
    result = score(
        ["e1", "e2"],
        [ev("e1", EvidenceKind.COMMIT), ev("e2", EvidenceKind.ISSUE)],
    )

    assert result.confidence is Confidence.LOW


def test_medium_for_one_strong_or_two_of_the_same_kind() -> None:
    one_strong = score(["e1"], [ev("e1", EvidenceKind.TEST)])
    same_kind = score(
        ["e1", "e2"],
        [ev("e1", EvidenceKind.CODE), ev("e2", EvidenceKind.CODE, path="b.py")],
    )

    assert one_strong.confidence is Confidence.MEDIUM
    assert same_kind.confidence is Confidence.MEDIUM


def test_low_for_a_single_indirect_evidence() -> None:
    result = score(["e1"], [ev("e1", EvidenceKind.COMMIT)])

    assert result.confidence is Confidence.LOW
    assert result.confidence_rationale == "1 evidência verificada: commit `abc1234`."


def test_a_failed_evidence_downgrades_one_level_and_the_status() -> None:
    result = score(
        ["e1", "e2", "e3"],
        [
            ev("e1", EvidenceKind.CODE),
            ev("e2", EvidenceKind.COMMIT),
            ev("e3", EvidenceKind.CODE, VerifyStatus.FAILED, path="c.py"),
        ],
    )

    assert result.confidence is Confidence.MEDIUM
    assert result.status is FindingStatus.DOWNGRADED
    assert "não passaram na verificação" in result.confidence_rationale


def test_downgrade_never_goes_below_low() -> None:
    result = score(
        ["e1", "e2"],
        [
            ev("e1", EvidenceKind.COMMIT),
            ev("e2", EvidenceKind.CODE, VerifyStatus.FAILED),
        ],
    )

    assert result.confidence is Confidence.LOW
    assert result.status is FindingStatus.DOWNGRADED


def test_contradicting_evidence_caps_confidence_at_medium() -> None:
    evidences = [ev("e1", EvidenceKind.CODE), ev("e2", EvidenceKind.COMMIT)]

    result = score(["e1", "e2"], evidences, contradicting=["e9"])

    assert result.confidence is Confidence.MEDIUM
    assert result.status is FindingStatus.KEPT


def test_contradiction_does_not_raise_a_low_confidence() -> None:
    result = score(["e1"], [ev("e1", EvidenceKind.COMMIT)], contradicting=["e9"])

    assert result.confidence is Confidence.LOW


def test_scoring_does_not_mutate_the_input_finding() -> None:
    original = finding(["e1"])

    score_finding(original, {"e1": ev("e1", EvidenceKind.CODE)})

    assert original.confidence is None
    assert original.status is FindingStatus.KEPT

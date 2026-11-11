from pathlib import Path

import pytest
from app.tools.repo_source import create_isolated_git, validate_repo_path


def test_accepts_repository_inside_allowed_root(tmp_path: Path) -> None:
    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    result = validate_repo_path(repository, [allowed_root])

    assert result == repository.resolve()


def test_rejects_repository_outside_allowed_roots(tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    outside_repository = tmp_path / "outside"
    allowed_root.mkdir()
    outside_repository.mkdir()

    with pytest.raises(PermissionError):
        validate_repo_path(outside_repository, [allowed_root])


def test_rejects_nonexistent_repository(tmp_path: Path) -> None:
    allowed_root = tmp_path / "repos"
    allowed_root.mkdir()

    with pytest.raises((FileNotFoundError, NotADirectoryError)):
        validate_repo_path(allowed_root / "missing", [allowed_root])


def test_rejects_symlink_repository_path(tmp_path: Path) -> None:
    allowed_root = tmp_path / "repos"
    outside_repository = tmp_path / "outside"
    allowed_root.mkdir()
    outside_repository.mkdir()

    link = allowed_root / "linked"
    link.symlink_to(outside_repository, target_is_directory=True)

    with pytest.raises(PermissionError):
        validate_repo_path(link, [allowed_root])


def test_rejects_inherited_git_environment_variable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_DIR", "/tmp/untrusted-git-dir")

    with pytest.raises(RuntimeError, match="GIT_DIR"):
        create_isolated_git(tmp_path)


def test_isolated_git_applies_security_options(tmp_path: Path) -> None:
    git = create_isolated_git(tmp_path)

    assert git.config("--get", "core.hooksPath") == "/dev/null"
    assert git.config("--get", "core.fsmonitor") == "false"
    assert git.version()


def test_local_repo_source_opens_allowed_repository(tmp_path: Path) -> None:
    from app.tools.repo_source import LocalRepoSource, create_isolated_git

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)
    create_isolated_git(repository).init()

    source = LocalRepoSource(repository, [allowed_root])

    assert source.repository_path == repository.resolve()


def test_local_repo_source_rejects_non_git_directory(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    with pytest.raises(ValueError, match="Git repository"):
        LocalRepoSource(repository, [allowed_root])


def create_committed_repository(
    repository: Path,
    content: str = "conteudo de teste\n",
) -> str:
    from git import Repo

    repo = Repo.init(repository)
    tracked_file = repository / "example.txt"
    tracked_file.write_text(content, encoding="utf-8")
    repo.index.add(["example.txt"])
    commit = repo.index.commit("Initial commit")

    return commit.hexsha


def test_local_repo_source_resolves_head(tmp_path: Path) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)
    expected_commit = create_committed_repository(repository)

    source = LocalRepoSource(repository, [allowed_root])

    assert source.resolve_commit(None) == expected_commit


def test_local_repo_source_resolves_branch(tmp_path: Path) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)
    expected_commit = create_committed_repository(repository)

    source = LocalRepoSource(repository, [allowed_root])

    assert source.resolve_commit("master") == expected_commit


def test_local_repo_source_rejects_unknown_reference(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)
    create_committed_repository(repository)

    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(ValueError, match="commit reference"):
        source.resolve_commit("branch-that-does-not-exist")


def test_local_repo_source_rejects_option_like_reference(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)
    create_committed_repository(repository)

    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(ValueError, match="commit reference"):
        source.resolve_commit("--help")


def test_local_repo_source_reads_file_from_pinned_commit(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)
    commit = create_committed_repository(repository)

    (repository / "example.txt").write_text(
        "conteudo alterado\n",
        encoding="utf-8",
    )

    source = LocalRepoSource(repository, [allowed_root])

    assert source.read_file(commit, "example.txt") == "conteudo de teste\n"


def test_local_repo_source_rejects_parent_directory_path(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)
    commit = create_committed_repository(repository)
    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(PermissionError):
        source.read_file(commit, "../outside.txt")


def test_local_repo_source_rejects_absolute_path(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)
    commit = create_committed_repository(repository)
    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(PermissionError):
        source.read_file(commit, "/etc/passwd")


def test_local_repo_source_rejects_sensitive_file(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    (repository / ".env").write_text(
        "API_KEY=example\n",
        encoding="utf-8",
    )
    from git import Repo

    repo = Repo.init(repository)
    repo.index.add([".env"])
    commit = repo.index.commit("Add sensitive file").hexsha

    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(PermissionError):
        source.read_file(commit, ".env")


def test_local_repo_source_rejects_symlink_file(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource
    from git import Repo

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    target = repository / "target.txt"
    target.write_text("conteudo externo\n", encoding="utf-8")

    link = repository / "linked.txt"
    link.symlink_to(target.name)

    repo = Repo.init(repository)
    repo.index.add(["target.txt", "linked.txt"])
    commit = repo.index.commit("Add symbolic link").hexsha

    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(PermissionError):
        source.read_file(commit, "linked.txt")


def test_local_repo_source_rejects_file_over_one_megabyte(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource
    from git import Repo

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    large_file = repository / "large.txt"
    large_file.write_text("A" * 1_000_001, encoding="utf-8")

    repo = Repo.init(repository)
    repo.index.add(["large.txt"])
    commit = repo.index.commit("Add large file").hexsha

    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(PermissionError, match="1 MB"):
        source.read_file(commit, "large.txt")


def test_local_repo_source_rejects_non_utf8_file(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource
    from git import Repo

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    binary_file = repository / "binary.dat"
    binary_file.write_bytes(b"\xff\xfe\x00\x01")

    repo = Repo.init(repository)
    repo.index.add(["binary.dat"])
    commit = repo.index.commit("Add binary file").hexsha

    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(ValueError, match="UTF-8"):
        source.read_file(commit, "binary.dat")


def test_local_repo_source_rejects_internal_parent_directory_path(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)
    commit = create_committed_repository(repository)
    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(PermissionError):
        source.read_file(commit, "pasta/../example.txt")


def test_local_repo_source_rejects_backslash_path(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)
    commit = create_committed_repository(repository)
    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(PermissionError):
        source.read_file(commit, r"pasta\example.txt")


def test_local_repo_source_rejects_null_byte_in_path(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)
    commit = create_committed_repository(repository)
    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(PermissionError):
        source.read_file(commit, "example.txt\x00")


def test_local_repo_source_rejects_null_byte_in_commit_reference(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)
    create_committed_repository(repository)
    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(ValueError, match="commit reference"):
        source.resolve_commit("HEAD\x00")


def test_local_repo_source_lists_files_from_pinned_commit(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource
    from git import Repo

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    (repository / "example.txt").write_text("conteudo\n", encoding="utf-8")
    (repository / "src").mkdir()
    (repository / "src" / "app.py").write_text(
        "print('hello')\n",
        encoding="utf-8",
    )

    repo = Repo.init(repository)
    repo.index.add(["example.txt", "src/app.py"])
    commit = repo.index.commit("Initial commit").hexsha

    (repository / "untracked.txt").write_text(
        "nao deve aparecer\n",
        encoding="utf-8",
    )

    source = LocalRepoSource(repository, [allowed_root])

    assert source.list_files(commit) == ["example.txt", "src/app.py"]


def test_local_repo_source_lists_files_from_old_commit(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource
    from git import Repo

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    (repository / "old.txt").write_text("antigo\n", encoding="utf-8")

    repo = Repo.init(repository)
    repo.index.add(["old.txt"])
    old_commit = repo.index.commit("Initial commit").hexsha

    (repository / "new.txt").write_text("novo\n", encoding="utf-8")
    repo.index.add(["new.txt"])
    repo.index.remove(["old.txt"])
    repo.index.commit("Replace old file")

    source = LocalRepoSource(repository, [allowed_root])

    assert source.list_files(old_commit) == ["old.txt"]


def test_local_repo_source_rejects_invalid_commit_when_listing_files(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)
    create_committed_repository(repository)

    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(ValueError, match="commit SHA"):
        source.list_files("invalid")


def test_local_repo_source_returns_git_log(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource
    from git import Repo

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    tracked_file = repository / "example.txt"
    tracked_file.write_text("primeira versao\n", encoding="utf-8")

    repo = Repo.init(repository)
    repo.index.add(["example.txt"])
    first_commit = repo.index.commit("Initial commit")

    tracked_file.write_text("segunda versao\n", encoding="utf-8")
    repo.index.add(["example.txt"])
    second_commit = repo.index.commit("Update example")

    source = LocalRepoSource(repository, [allowed_root])

    result = source.git_log(second_commit.hexsha, None, 20)

    assert len(result) == 2
    assert result[0]["sha"] == second_commit.hexsha
    assert result[0]["assunto"] == "Update example"
    assert result[1]["sha"] == first_commit.hexsha
    assert result[1]["assunto"] == "Initial commit"


def test_local_repo_source_filters_git_log_by_path(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource
    from git import Repo

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    (repository / "first.txt").write_text("primeiro\n", encoding="utf-8")
    (repository / "second.txt").write_text("segundo\n", encoding="utf-8")

    repo = Repo.init(repository)
    repo.index.add(["first.txt", "second.txt"])
    repo.index.commit("Add both files")

    (repository / "first.txt").write_text("alterado\n", encoding="utf-8")
    repo.index.add(["first.txt"])
    expected_commit = repo.index.commit("Update first file")

    source = LocalRepoSource(repository, [allowed_root])

    result = source.git_log(expected_commit.hexsha, "first.txt", 20)

    assert len(result) == 2
    assert all(isinstance(item["sha"], str) for item in result)


def test_local_repo_source_rejects_invalid_git_log_limit(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)
    commit = create_committed_repository(repository)

    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(ValueError):
        source.git_log(commit, None, 21)


def test_local_repo_source_git_log_respects_pinned_commit(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource
    from git import Repo

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    tracked_file = repository / "example.txt"
    tracked_file.write_text("primeira versao\n", encoding="utf-8")

    repo = Repo.init(repository)
    repo.index.add(["example.txt"])
    first_commit = repo.index.commit("Initial commit")

    tracked_file.write_text("segunda versao\n", encoding="utf-8")
    repo.index.add(["example.txt"])
    repo.index.commit("Second commit")

    source = LocalRepoSource(repository, [allowed_root])

    result = source.git_log(first_commit.hexsha, None, 20)

    assert len(result) == 1
    assert result[0]["sha"] == first_commit.hexsha


def test_local_repo_source_git_blame_attributes_each_line(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource
    from git import Repo

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    tracked_file = repository / "example.txt"
    tracked_file.write_text("linha um\nlinha dois\n", encoding="utf-8")

    repo = Repo.init(repository)
    repo.index.add(["example.txt"])
    first_commit = repo.index.commit("Add example")

    tracked_file.write_text(
        "linha um\nlinha dois alterada\n",
        encoding="utf-8",
    )
    repo.index.add(["example.txt"])
    second_commit = repo.index.commit("Update example")

    source = LocalRepoSource(repository, [allowed_root])

    result = source.git_blame(second_commit.hexsha, "example.txt", 1, 2)

    assert len(result) == 2
    assert result[0]["linha"] == 1
    assert result[0]["sha"] == first_commit.hexsha
    assert result[1]["linha"] == 2
    assert result[1]["sha"] == second_commit.hexsha
    assert all(item["autor"] for item in result)


def test_local_repo_source_git_blame_respects_line_range(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    tracked_file = repository / "example.txt"
    tracked_file.write_text("um\ndois\ntres\nquatro\n", encoding="utf-8")
    commit = create_committed_repository(
        repository,
        "um\ndois\ntres\nquatro\n",
    )

    source = LocalRepoSource(repository, [allowed_root])

    result = source.git_blame(commit, "example.txt", 2, 3)

    assert len(result) == 2
    assert [item["linha"] for item in result] == [2, 3]


def test_local_repo_source_git_blame_respects_pinned_commit(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource
    from git import Repo

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    tracked_file = repository / "example.txt"
    tracked_file.write_text("versao original\n", encoding="utf-8")

    repo = Repo.init(repository)
    repo.index.add(["example.txt"])
    first_commit = repo.index.commit("Initial commit")

    tracked_file.write_text("versao modificada\n", encoding="utf-8")
    repo.index.add(["example.txt"])
    repo.index.commit("Update example")

    source = LocalRepoSource(repository, [allowed_root])

    result = source.git_blame(first_commit.hexsha, "example.txt", 1, 1)

    assert len(result) == 1
    assert result[0]["sha"] == first_commit.hexsha


def test_local_repo_source_git_blame_rejects_more_than_60_lines(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    tracked_file = repository / "example.txt"
    tracked_file.write_text(
        "".join(f"linha {number}\n" for number in range(1, 62)),
        encoding="utf-8",
    )
    commit = create_committed_repository(repository)

    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(ValueError):
        source.git_blame(commit, "example.txt", 1, 61)


def test_local_repo_source_git_blame_rejects_invalid_line_range(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    commit = create_committed_repository(repository)
    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(ValueError):
        source.git_blame(commit, "example.txt", 0, 1)


def test_local_repo_source_returns_diff_between_pinned_commits(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource
    from git import Repo

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    tracked_file = repository / "example.txt"
    tracked_file.write_text("versao um\n", encoding="utf-8")

    repo = Repo.init(repository)
    repo.index.add(["example.txt"])
    base_commit = repo.index.commit("Initial commit")

    tracked_file.write_text("versao dois\n", encoding="utf-8")
    repo.index.add(["example.txt"])
    head_commit = repo.index.commit("Update example")

    source = LocalRepoSource(repository, [allowed_root])

    result = source.diff(base_commit.hexsha, head_commit.hexsha, None)

    assert "-versao um" in result
    assert "+versao dois" in result


def test_local_repo_source_diff_filters_by_path(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource
    from git import Repo

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    first_file = repository / "first.txt"
    second_file = repository / "second.txt"
    first_file.write_text("antigo primeiro\n", encoding="utf-8")
    second_file.write_text("antigo segundo\n", encoding="utf-8")

    repo = Repo.init(repository)
    repo.index.add(["first.txt", "second.txt"])
    base_commit = repo.index.commit("Initial commit")

    first_file.write_text("novo primeiro\n", encoding="utf-8")
    second_file.write_text("novo segundo\n", encoding="utf-8")
    repo.index.add(["first.txt", "second.txt"])
    head_commit = repo.index.commit("Update files")

    source = LocalRepoSource(repository, [allowed_root])

    result = source.diff(
        base_commit.hexsha,
        head_commit.hexsha,
        "first.txt",
    )

    assert "novo primeiro" in result
    assert "novo segundo" not in result


def test_local_repo_source_diff_respects_pinned_commits(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource
    from git import Repo

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    tracked_file = repository / "example.txt"
    tracked_file.write_text("versao um\n", encoding="utf-8")

    repo = Repo.init(repository)
    repo.index.add(["example.txt"])
    base_commit = repo.index.commit("Initial commit")

    tracked_file.write_text("versao dois\n", encoding="utf-8")
    repo.index.add(["example.txt"])
    head_commit = repo.index.commit("Second commit")

    source = LocalRepoSource(repository, [allowed_root])

    tracked_file.write_text("versao tres\n", encoding="utf-8")
    repo.index.add(["example.txt"])
    repo.index.commit("Third commit")

    result = source.diff(base_commit.hexsha, head_commit.hexsha, None)

    assert "+versao dois" in result
    assert "versao tres" not in result


def test_local_repo_source_diff_rejects_invalid_commit(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    commit = create_committed_repository(repository)
    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(ValueError):
        source.diff("invalid", commit, None)


def test_local_repo_source_diff_rejects_invalid_path(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    commit = create_committed_repository(repository)
    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(PermissionError):
        source.diff(commit, commit, "../outside.txt")


def test_local_repo_source_diff_rejects_sensitive_path(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource
    from git import Repo

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    secret_file = repository / ".env"
    secret_file.write_text("API_KEY=secret\n", encoding="utf-8")

    repo = Repo.init(repository)
    repo.index.add([".env"])
    commit = repo.index.commit("Add environment file")

    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(PermissionError, match="Sensitive"):
        source.diff(commit.hexsha, commit.hexsha, ".env")


def test_local_repo_source_diff_excludes_sensitive_files(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource
    from git import Repo

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    safe_file = repository / "example.txt"
    secret_file = repository / ".env"

    safe_file.write_text("conteudo antigo\n", encoding="utf-8")
    secret_file.write_text("API_KEY=old-secret\n", encoding="utf-8")

    repo = Repo.init(repository)
    repo.index.add(["example.txt", ".env"])
    base_commit = repo.index.commit("Initial commit")

    safe_file.write_text("conteudo novo\n", encoding="utf-8")
    secret_file.write_text("API_KEY=new-secret\n", encoding="utf-8")

    repo.index.add(["example.txt", ".env"])
    head_commit = repo.index.commit("Update files")

    source = LocalRepoSource(repository, [allowed_root])

    result = source.diff(base_commit.hexsha, head_commit.hexsha, None)

    assert "conteudo novo" in result
    assert "old-secret" not in result
    assert "new-secret" not in result


def test_local_repo_source_read_file_rejects_git_path(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    commit = create_committed_repository(repository)
    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(PermissionError, match="Invalid repository path"):
        source.read_file(commit, ".git/config")


def test_local_repo_source_git_blame_rejects_git_path(
    tmp_path: Path,
) -> None:
    from app.tools.repo_source import LocalRepoSource

    allowed_root = tmp_path / "repos"
    repository = allowed_root / "project"
    repository.mkdir(parents=True)

    commit = create_committed_repository(repository)
    source = LocalRepoSource(repository, [allowed_root])

    with pytest.raises(PermissionError, match="Invalid repository path"):
        source.git_blame(commit, ".git/config", 1, 1)

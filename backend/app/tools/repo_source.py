import os
from pathlib import Path

from git import Git

GIT_CONFIG_OPTIONS = [
    "core.hooksPath=/dev/null",
    "core.fsmonitor=false",
    "filter.lfs.smudge=",
    "filter.lfs.process=",
]


def is_git_metadata_path(path: str) -> bool:
    return any(part.lower() == ".git" for part in path.split("/"))


def validate_repo_path(
    repository_path: Path,
    allowed_roots: list[Path],
) -> Path:
    if repository_path.is_symlink():
        raise PermissionError("Repository path cannot be a symbolic link")

    resolved_repository = repository_path.resolve(strict=True)

    if not resolved_repository.is_dir():
        raise NotADirectoryError("Repository path is not a directory")

    for root in allowed_roots:
        if root.is_symlink():
            continue

        resolved_root = root.resolve(strict=True)

        if not resolved_root.is_dir():
            continue

        try:
            resolved_repository.relative_to(resolved_root)
            return resolved_repository
        except ValueError:
            continue

    raise PermissionError("Repository is outside allowed roots")


def create_isolated_git(repository_path: Path) -> Git:
    inherited_git_variables = sorted(
        variable for variable in os.environ if variable.startswith("GIT_")
    )
    if inherited_git_variables:
        names = ", ".join(inherited_git_variables)
        raise RuntimeError(f"Unexpected Git environment variables: {names}")

    git = Git(working_dir=str(repository_path))

    git_environment = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
    }
    git.update_environment(**git_environment)
    git.set_persistent_git_options(c=GIT_CONFIG_OPTIONS)

    return git


class LocalRepoSource:
    def __init__(
        self,
        repository_path: Path,
        allowed_roots: list[Path],
    ) -> None:
        self.repository_path = validate_repo_path(
            repository_path,
            allowed_roots,
        )

        git_directory = self.repository_path / ".git"

        if git_directory.is_symlink() or not git_directory.is_dir():
            raise ValueError("Path is not a supported Git repository")

        self._git = create_isolated_git(self.repository_path)

        try:
            is_work_tree = self._git.rev_parse("--is-inside-work-tree")
            repository_root = Path(self._git.rev_parse("--show-toplevel")).resolve(
                strict=True
            )
        except Exception as exc:
            raise ValueError("Path is not a valid Git repository") from exc

        if is_work_tree != "true" or repository_root != self.repository_path:
            raise ValueError("Path is not a valid Git repository")

    def resolve_commit(self, ref: str | None = None) -> str:
        if ref is None:
            ref = "HEAD"

        if (
            not isinstance(ref, str)
            or not ref
            or len(ref) > 256
            or ref != ref.strip()
            or ref.startswith("-")
            or "\x00" in ref
        ):
            raise ValueError("Invalid commit reference")

        try:
            commit = self._git.rev_parse(
                "--verify",
                "--end-of-options",
                f"{ref}^{{commit}}",
            )
        except Exception as exc:
            raise ValueError("Invalid commit reference") from exc

        if len(commit) != 40 or any(
            character not in "0123456789abcdef" for character in commit
        ):
            raise ValueError("Git returned an invalid commit SHA")

        return str(commit)

    def read_file(self, commit: str, path: str) -> str:
        import posixpath

        from git.exc import GitCommandError

        if (
            not isinstance(commit, str)
            or len(commit) != 40
            or any(character not in "0123456789abcdef" for character in commit)
        ):
            raise ValueError("Invalid commit SHA")

        if (
            not isinstance(path, str)
            or not path
            or "\x00" in path
            or path.startswith("/")
            or "\\" in path
        ):
            raise PermissionError("Invalid repository path")

        raw_parts = path.split("/")
        if any(part in {"", ".", ".."} for part in raw_parts):
            raise PermissionError("Invalid repository path")
        if is_git_metadata_path(path):
            raise PermissionError("Invalid repository path")

        normalized_path = posixpath.normpath(path)

        if normalized_path in {".", ".."} or normalized_path.startswith("../"):
            raise PermissionError("Repository path traversal is not allowed")

        parts = normalized_path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise PermissionError("Invalid repository path")

        filename = parts[-1].lower()
        if (
            filename.startswith(".env")
            or filename.endswith((".pem", ".key"))
            or filename.startswith("id_rsa")
            or filename
            in {
                "credentials",
                "credentials.json",
                "service-account.json",
            }
        ):
            raise PermissionError("Sensitive file access is denied")

        literal_path = f":(literal){normalized_path}"

        try:
            tree_entry = self._git.ls_tree(
                "-z",
                commit,
                "--",
                literal_path,
            )
        except GitCommandError as exc:
            raise ValueError("Unable to inspect commit tree") from exc

        entries = [entry for entry in tree_entry.split("\0") if entry]
        if len(entries) != 1:
            raise FileNotFoundError("File does not exist in the selected commit")

        metadata, separator, entry_path = entries[0].partition("\t")
        fields = metadata.split()

        if not separator or entry_path != normalized_path or len(fields) != 3:
            raise FileNotFoundError("File does not exist in the selected commit")

        mode, object_type, object_sha = fields

        if object_type != "blob" or mode not in {"100644", "100755"}:
            raise PermissionError("Only regular files can be read")

        try:
            size = int(self._git.cat_file("-s", object_sha))
        except (GitCommandError, ValueError) as exc:
            raise ValueError("Unable to inspect file object") from exc

        if size > 1_000_000:
            raise PermissionError("File exceeds the 1 MB read limit")

        process = self._git.cat_file(
            "blob",
            object_sha,
            as_process=True,
        )
        stdout, _ = process.communicate()

        if process.returncode != 0:
            raise ValueError("Unable to read file object")

        try:
            return str(stdout.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValueError("File is not valid UTF-8 text") from exc

    def list_files(self, commit: str) -> list[str]:
        from git.exc import GitCommandError

        if (
            not isinstance(commit, str)
            or len(commit) != 40
            or any(character not in "0123456789abcdef" for character in commit)
        ):
            raise ValueError("Invalid commit SHA")

        try:
            tree_entries = self._git.ls_tree(
                "-r",
                "-z",
                "--full-tree",
                commit,
            )
        except GitCommandError as exc:
            raise ValueError("Unable to list commit files") from exc

        paths = []

        for entry in tree_entries.split("\0"):
            if not entry:
                continue

            metadata, separator, entry_path = entry.partition("\t")
            fields = metadata.split()

            if not separator or len(fields) != 3:
                raise ValueError("Git returned an invalid tree entry")

            mode, object_type, _object_sha = fields

            if object_type != "blob" or mode not in {"100644", "100755"}:
                continue

            paths.append(entry_path)

        return sorted(paths)

    def git_log(
        self,
        commit: str,
        path: str | None,
        limit: int,
    ) -> list[dict[str, str]]:
        from git.exc import GitCommandError

        if (
            not isinstance(commit, str)
            or len(commit) != 40
            or any(character not in "0123456789abcdef" for character in commit)
        ):
            raise ValueError("Invalid commit SHA")

        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 20
        ):
            raise ValueError("Git log limit must be between 1 and 20")

        arguments = [
            "--format=%H%x00%an%x00%aI%x00%s",
            "-n",
            str(limit),
            commit,
        ]

        if path is not None:
            import posixpath

            if (
                not isinstance(path, str)
                or not path
                or "\x00" in path
                or path.startswith("/")
                or "\\" in path
            ):
                raise PermissionError("Invalid repository path")

            raw_parts = path.split("/")
            if any(part in {"", ".", ".."} for part in raw_parts):
                raise PermissionError("Invalid repository path")
            if is_git_metadata_path(path):
                raise PermissionError("Invalid repository path")

            normalized_path = posixpath.normpath(path)
            arguments.extend(["--", f":(literal){normalized_path}"])

        try:
            output = self._git.log(*arguments)
        except GitCommandError as exc:
            raise ValueError("Unable to read Git history") from exc

        results = []

        for line in output.splitlines():
            if not line:
                continue

            fields = line.split("\0")
            if len(fields) != 4:
                raise ValueError("Git returned an invalid log entry")

            sha, author, date, subject = fields

            if len(sha) != 40 or any(
                character not in "0123456789abcdef" for character in sha
            ):
                raise ValueError("Git returned an invalid commit SHA")

            results.append(
                {
                    "sha": sha,
                    "autor": author,
                    "data": date,
                    "assunto": subject,
                }
            )

        return results

    def git_blame(
        self,
        commit: str,
        path: str,
        start: int,
        end: int,
    ) -> list[dict[str, str | int]]:
        import posixpath

        from git.exc import GitCommandError

        if (
            not isinstance(commit, str)
            or len(commit) != 40
            or any(character not in "0123456789abcdef" for character in commit)
        ):
            raise ValueError("Invalid commit SHA")

        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 1
            or end < start
        ):
            raise ValueError("Invalid Git blame line range")

        if end - start + 1 > 60:
            raise ValueError("Git blame limit is 60 lines")

        if (
            not isinstance(path, str)
            or not path
            or "\x00" in path
            or path.startswith("/")
            or "\\" in path
        ):
            raise PermissionError("Invalid repository path")

        raw_parts = path.split("/")
        if any(part in {"", ".", ".."} for part in raw_parts):
            raise PermissionError("Invalid repository path")
        if is_git_metadata_path(path):
            raise PermissionError("Invalid repository path")

        normalized_path = posixpath.normpath(path)
        filename = normalized_path.split("/")[-1].lower()

        if (
            filename.startswith(".env")
            or filename.endswith((".pem", ".key"))
            or filename.startswith("id_rsa")
            or filename
            in {
                "credentials",
                "credentials.json",
                "service-account.json",
            }
        ):
            raise PermissionError("Sensitive file access is denied")

        try:
            output = self._git.blame(
                "--line-porcelain",
                "--no-textconv",
                "-L",
                f"{start},{end}",
                commit,
                "--",
                normalized_path,
                env={"GIT_LITERAL_PATHSPECS": "1"},
            )
        except GitCommandError as exc:
            raise ValueError("Unable to read Git blame") from exc

        results: list[dict[str, str | int]] = []
        current_sha = ""
        current_line = 0
        current_author: str | None = None

        for line in output.splitlines():
            if line.startswith("\t"):
                if not current_sha or current_author is None or current_line < 1:
                    raise ValueError("Git returned an invalid blame line")

                results.append(
                    {
                        "linha": current_line,
                        "sha": current_sha,
                        "autor": current_author,
                        "conteudo": line[1:],
                    }
                )
                continue

            fields = line.split()
            if len(fields) >= 3 and fields[1].isdigit() and fields[2].isdigit():
                sha = fields[0].lstrip("^")
                if len(sha) != 40 or any(
                    char not in "0123456789abcdef" for char in sha
                ):
                    raise ValueError("Git returned an invalid blame SHA")

                current_sha = sha
                current_line = int(fields[2])
                current_author = None
            elif line.startswith("author "):
                current_author = line[len("author ") :]

        if len(results) != end - start + 1:
            raise ValueError("Git returned an incomplete blame result")

        return results

    def diff(
        self,
        base: str,
        head: str,
        path: str | None,
    ) -> str:
        import posixpath

        from git.exc import GitCommandError

        for commit in (base, head):
            if (
                not isinstance(commit, str)
                or len(commit) != 40
                or any(character not in "0123456789abcdef" for character in commit)
            ):
                raise ValueError("Invalid commit SHA")

        arguments = [
            "--no-ext-diff",
            "--no-textconv",
            base,
            head,
        ]

        if path is not None:
            if (
                not isinstance(path, str)
                or not path
                or "\x00" in path
                or path.startswith("/")
                or "\\\\" in path
            ):
                raise PermissionError("Invalid repository path")

            raw_parts = path.split("/")
            if any(part in {"", ".", ".."} for part in raw_parts):
                raise PermissionError("Invalid repository path")
            if is_git_metadata_path(path):
                raise PermissionError("Invalid repository path")

            normalized_path = posixpath.normpath(path)
            filename = normalized_path.rsplit("/", 1)[-1].lower()

            if (
                filename.startswith(".env")
                or filename.endswith((".pem", ".key"))
                or filename.startswith("id_rsa")
                or filename
                in {
                    "credentials",
                    "credentials.json",
                    "service-account.json",
                }
            ):
                raise PermissionError("Sensitive file access is denied")

            arguments.extend(["--", normalized_path])
        else:
            try:
                changed_paths = self._git.diff(
                    "--name-only",
                    "-z",
                    base,
                    head,
                )
            except GitCommandError as exc:
                raise ValueError("Unable to compare Git commits") from exc

            safe_paths = []

            for changed_path in changed_paths.split("\0"):
                if not changed_path:
                    continue

                filename = changed_path.rsplit("/", 1)[-1].lower()

                if (
                    filename.startswith(".env")
                    or filename.endswith((".pem", ".key"))
                    or filename.startswith("id_rsa")
                    or filename
                    in {
                        "credentials",
                        "credentials.json",
                        "service-account.json",
                    }
                ):
                    continue

                safe_paths.append(changed_path)

            if not safe_paths:
                return ""

            arguments.extend(["--", *safe_paths])

        try:
            return str(
                self._git.diff(
                    *arguments,
                    env={"GIT_LITERAL_PATHSPECS": "1"},
                )
            )
        except GitCommandError as exc:
            raise ValueError("Unable to compare Git commits") from exc

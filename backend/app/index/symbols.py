from __future__ import annotations

from app.index.store import IndexStore


class SymbolSearch:
    def __init__(self, store: IndexStore) -> None:
        self.store = store

    def find_symbol(
        self,
        repo_id: str,
        commit_sha: str,
        name: str,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("Invalid symbol name")

        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 20
        ):
            raise ValueError("limit must be between 1 and 20")

        sql = """
            SELECT path, name, kind, line_start, line_end
            FROM symbols
            WHERE repo_id = ? AND commit_sha = ? AND name = ?
        """
        parameters: list[object] = [repo_id, commit_sha, name]

        if kind is not None:
            if not isinstance(kind, str) or not kind.strip():
                raise ValueError("Invalid symbol kind")
            sql += " AND kind = ?"
            parameters.append(kind)

        sql += " ORDER BY path, line_start LIMIT ?"
        parameters.append(limit)

        rows = self.store.connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]

    def list_symbols(
        self,
        repo_id: str,
        commit_sha: str,
        path: str,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        if not isinstance(path, str) or not path or path.startswith("/"):
            raise ValueError("Invalid repository path")

        if ".." in path.split("/") or "\\" in path or "\x00" in path:
            raise ValueError("Invalid repository path")

        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("limit must be between 1 and 100")

        rows = self.store.connection.execute(
            """
            SELECT name, kind, line_start, line_end
            FROM symbols
            WHERE repo_id = ? AND commit_sha = ? AND path = ?
            ORDER BY line_start
            LIMIT ?
            """,
            (repo_id, commit_sha, path, limit),
        ).fetchall()

        return [dict(row) for row in rows]

    def find_references(
        self,
        repo_id: str,
        commit_sha: str,
        symbol: str,
        limit: int = 10,
    ) -> dict[str, object]:
        if not isinstance(symbol, str) or not symbol.strip() or len(symbol) > 200:
            raise ValueError("Invalid symbol name")

        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 20
        ):
            raise ValueError("limit must be between 1 and 20")

        rows = self.store.connection.execute(
            """
            SELECT path, name, kind, line
            FROM references_index
            WHERE repo_id = ? AND commit_sha = ? AND name = ?
            ORDER BY path, line
            LIMIT ?
            """,
            (repo_id, commit_sha, symbol, limit),
        ).fetchall()

        return {
            "precision": "approximate",
            "references": [dict(row) for row in rows],
        }

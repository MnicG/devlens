from __future__ import annotations

import re

from app.index.store import IndexStore


class CodeSearch:
    def __init__(self, store: IndexStore) -> None:
        self.store = store

    def search(
        self,
        repo_id: str,
        commit_sha: str,
        query: str,
        top_k: int = 8,
        path_glob: str | None = None,
    ) -> list[dict[str, object]]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query must not be empty")

        if len(query) > 200:
            raise ValueError("Query exceeds 200 characters")

        if (
            isinstance(top_k, bool)
            or not isinstance(top_k, int)
            or not 1 <= top_k <= 20
        ):
            raise ValueError("top_k must be between 1 and 20")

        terms = re.findall(r"[A-Za-z0-9_]+", query)

        if not terms:
            return []

        match_query = " AND ".join(
            f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms
        )

        sql = """
            SELECT
                path,
                line_start,
                line_end,
                content,
                bm25(code_chunks) AS score
            FROM code_chunks
            WHERE code_chunks MATCH ?
              AND repo_id = ?
              AND commit_sha = ?
        """
        parameters: list[object] = [
            match_query,
            repo_id,
            commit_sha,
        ]

        if path_glob is not None:
            if (
                not isinstance(path_glob, str)
                or not path_glob.strip()
                or len(path_glob) > 200
                or path_glob.startswith("/")
                or "\\" in path_glob
                or "\x00" in path_glob
                or ".." in path_glob.split("/")
            ):
                raise ValueError("Invalid path_glob")

            sql += " AND path GLOB ?"
            parameters.append(path_glob)

        sql += " ORDER BY score LIMIT ?"
        parameters.append(top_k)

        rows = self.store.connection.execute(
            sql,
            parameters,
        ).fetchall()

        return [
            {
                "path": row["path"],
                "line_start": row["line_start"],
                "line_end": row["line_end"],
                "snippet": "\n".join(row["content"].splitlines()[:3]),
                "score": row["score"],
            }
            for row in rows
        ]

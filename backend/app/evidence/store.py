"""Evidence Store: registra candidatos, atribui ids e guarda a verificação."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from app.evidence.candidates import make_excerpt
from app.evidence.hashing import evidence_id
from app.schemas.contracts import Evidence, EvidenceKind, VerifyStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evidences (
  id TEXT NOT NULL,
  analysis_id TEXT NOT NULL,
  repo TEXT NOT NULL,
  commit_sha TEXT NOT NULL,
  kind TEXT NOT NULL,
  path TEXT,
  line_start INTEGER,
  line_end INTEGER,
  snippet_hash TEXT,
  excerpt TEXT NOT NULL DEFAULT '',
  ref TEXT,
  verify_status TEXT NOT NULL DEFAULT 'pending',
  verify_note TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (analysis_id, id)
);
"""

_COLUMNS = (
    "id, repo, commit_sha, kind, path, line_start, line_end, "
    "snippet_hash, excerpt, ref, verify_status, verify_note"
)


def _row_to_evidence(row: sqlite3.Row) -> Evidence:
    return Evidence(
        id=row["id"],
        repo=row["repo"],
        commit=row["commit_sha"],
        kind=EvidenceKind(row["kind"]),
        path=row["path"],
        line_start=row["line_start"],
        line_end=row["line_end"],
        snippet_hash=row["snippet_hash"],
        excerpt=row["excerpt"],
        ref=row["ref"],
        verify_status=VerifyStatus(row["verify_status"]),
        verify_note=row["verify_note"],
    )


class EvidenceStore:
    def __init__(self, analysis_id: str, database_path: str | Path = ":memory:"):
        self.analysis_id = analysis_id
        self.connection = sqlite3.connect(str(database_path))
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(_SCHEMA)
        self.connection.commit()

    def register(self, candidate: Evidence) -> Evidence:
        """Registra o candidato; o mesmo trecho encontrado duas vezes é deduplicado."""
        new_id = evidence_id(
            candidate.repo,
            candidate.commit,
            candidate.kind.value,
            candidate.path,
            candidate.line_start,
            candidate.line_end,
            candidate.ref,
        )

        registered = candidate.model_copy(
            update={
                "id": new_id,
                # Defesa em profundidade: nunca persistir excerpt sem redação.
                "excerpt": make_excerpt(candidate.excerpt),
                "verify_status": VerifyStatus.PENDING,
                "verify_note": "",
            }
        )

        with self.connection:
            self.connection.execute(
                f"""
                INSERT OR IGNORE INTO evidences (analysis_id, {_COLUMNS})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.analysis_id,
                    registered.id,
                    registered.repo,
                    registered.commit,
                    registered.kind.value,
                    registered.path,
                    registered.line_start,
                    registered.line_end,
                    registered.snippet_hash,
                    registered.excerpt,
                    registered.ref,
                    registered.verify_status.value,
                    registered.verify_note,
                ),
            )

        stored = self.get(new_id)
        assert stored is not None
        return stored

    def get(self, evidence_id_: str) -> Evidence | None:
        row = self.connection.execute(
            f"SELECT {_COLUMNS} FROM evidences WHERE analysis_id = ? AND id = ?",
            (self.analysis_id, evidence_id_),
        ).fetchone()

        return _row_to_evidence(row) if row else None

    def all(self) -> list[Evidence]:
        rows = self.connection.execute(
            f"SELECT {_COLUMNS} FROM evidences WHERE analysis_id = ? ORDER BY rowid",
            (self.analysis_id,),
        ).fetchall()

        return [_row_to_evidence(row) for row in rows]

    def unknown_ids(self, ids: Iterable[str]) -> list[str]:
        """Ids que nenhuma ferramenta devolveu nesta análise (inventados)."""
        return [item for item in ids if self.get(item) is None]

    def set_verification(
        self,
        evidence_id_: str,
        status: VerifyStatus,
        note: str,
    ) -> None:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE evidences SET verify_status = ?, verify_note = ?
                WHERE analysis_id = ? AND id = ?
                """,
                (status.value, note, self.analysis_id, evidence_id_),
            )

        if cursor.rowcount != 1:
            raise KeyError("Unknown evidence id")

    def close(self) -> None:
        self.connection.close()

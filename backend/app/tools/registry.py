"""Conjunto padrão de ferramentas e fábrica do runner."""

from __future__ import annotations

from app.tools.base import Tool, ToolRunner
from app.tools.history import ChangedFiles, GetDiff, GitBlame, GitLog, GitShow
from app.tools.issues import RelatedIssues
from app.tools.navigation import (
    FindReferences,
    FindSymbol,
    ListSymbols,
    ReadRange,
    SearchCode,
)
from app.tools.testing import CheckTestScenario, FindTests


def default_tools() -> list[Tool]:
    return [
        SearchCode(),
        ReadRange(),
        FindSymbol(),
        ListSymbols(),
        FindReferences(),
        GitLog(),
        GitBlame(),
        GitShow(),
        ChangedFiles(),
        GetDiff(),
        FindTests(),
        CheckTestScenario(),
        RelatedIssues(),
    ]


def default_runner() -> ToolRunner:
    return ToolRunner(default_tools())

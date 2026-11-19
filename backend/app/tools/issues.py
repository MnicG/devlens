"""related_issues: depende de fonte com issues (GitHub, RF-55)."""

from __future__ import annotations

from typing import Annotated

from app.schemas.contracts import ToolResult
from app.tools.base import SOURCE_UNAVAILABLE, Tool, ToolArgs, ToolContext, ToolError
from pydantic import Field


class RelatedIssuesArgs(ToolArgs):
    query: Annotated[str, Field(min_length=1, max_length=200)]
    top_k: Annotated[int, Field(ge=1, le=10)] = 5


class RelatedIssues(Tool):
    name = "related_issues"
    args_schema = RelatedIssuesArgs

    def run(self, ctx: ToolContext, args: RelatedIssuesArgs) -> ToolResult:
        if getattr(ctx.source, "search_issues", None) is None:
            raise ToolError(
                SOURCE_UNAVAILABLE,
                "issues are only available for the GitHub source",
            )

        raise ToolError(SOURCE_UNAVAILABLE, "issue search is not implemented yet")

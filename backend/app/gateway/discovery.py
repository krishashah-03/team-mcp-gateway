"""Search-then-execute access to a large tool catalogue.

With ten servers the gateway can expose hundreds of tools, far too many to put in a
model's context. The lite endpoint offers three tools instead: search for tools by
intent, read one tool's full schema, then run it. Everything still goes through the
gateway's permission checks, timeouts and audit log.
"""

import re
from typing import Any

import mcp_types as types

_WORD = re.compile(r"[a-z0-9]+")
_SHORT_DESCRIPTION = 200

SEARCH_TOOLS = "search_tools"
DESCRIBE_TOOL = "describe_tool"
RUN_TOOL = "run_tool"

META_TOOLS: list[types.Tool] = [
    types.Tool(
        name=SEARCH_TOOLS,
        description=(
            "Find tools by describing what you want to do, e.g. 'list recent orders'. "
            "Returns the best matching tools with their full names. Use this first."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What you want to do"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 8},
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name=DESCRIBE_TOOL,
        description="Get the full description and input schema of one tool found with search_tools.",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Full tool name, e.g. woocommerce__list_orders"}},
            "required": ["name"],
        },
    ),
    types.Tool(
        name=RUN_TOOL,
        description="Run a tool by its full name with the arguments its schema requires.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Full tool name, e.g. woocommerce__list_orders"},
                "arguments": {"type": "object", "description": "Arguments for the tool", "default": {}},
            },
            "required": ["name"],
        },
    ),
]


def _terms(text: str) -> set[str]:
    return set(_WORD.findall(text.lower().replace("_", " ")))


def rank_tools(tools: list[types.Tool], query: str, limit: int = 8) -> list[types.Tool]:
    """Best-matching tools first. A hit in the name counts triple a hit in the description."""
    wanted = _terms(query)
    if not wanted:
        return []
    scored: list[tuple[int, str, types.Tool]] = []
    for tool in tools:
        name_terms = _terms(tool.name)
        description_terms = _terms(tool.description or "")
        score = 3 * len(wanted & name_terms) + len(wanted & description_terms)
        # Allow partial word matches, so 'order' finds 'orders'.
        score += sum(
            1 for w in wanted if len(w) > 3 and any(w in n or n in w for n in name_terms | description_terms)
        )
        if score > 0:
            scored.append((score, tool.name, tool))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [tool for _, _, tool in scored[:limit]]


def summarize(tool: types.Tool) -> dict[str, Any]:
    properties = tool.input_schema.get("properties", {}) if isinstance(tool.input_schema, dict) else {}
    required = set(tool.input_schema.get("required", [])) if isinstance(tool.input_schema, dict) else set()
    return {
        "name": tool.name,
        "description": (tool.description or "")[:_SHORT_DESCRIPTION],
        "arguments": [
            {"name": arg, "required": arg in required, "type": spec.get("type") if isinstance(spec, dict) else None}
            for arg, spec in properties.items()
        ],
    }

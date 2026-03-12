from types import SimpleNamespace

from vibrant.agents import PLANNING_COMPLETE_MCP_TOOL
from vibrant.tui.app import _extract_planning_completion_request


def test_planning_completion_is_detected_from_tool_events_only():
    result = SimpleNamespace(
        transcript=f"MCP: {PLANNING_COMPLETE_MCP_TOOL}",
        events=[{"tool_name": PLANNING_COMPLETE_MCP_TOOL}],
    )

    assert _extract_planning_completion_request(result) == PLANNING_COMPLETE_MCP_TOOL

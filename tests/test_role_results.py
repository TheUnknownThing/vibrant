from __future__ import annotations

from vibrant.agents.role_results import (
    GatekeeperRoleResult,
    TestRoleResult as AgentTestRoleResult,
    build_code_role_result,
    build_gatekeeper_role_result,
    parse_role_result,
    serialize_role_result,
)


def test_build_code_role_result_for_test_role() -> None:
    payload = build_code_role_result(
        role="test",
        transcript="Ran pytest.",
        summary="Ran pytest.",
        error=None,
        exit_code=0,
        awaiting_input=False,
        events=[
            {
                "type": "task.progress",
                "item": {"type": "commandExecution", "command": "pytest -q"},
            }
        ],
    )

    assert isinstance(payload, AgentTestRoleResult)
    assert payload.role == "test"
    assert payload.validation_passed is True
    assert payload.command_count == 1


def test_gatekeeper_role_result_extracts_tool_driven_decision() -> None:
    payload = build_gatekeeper_role_result(
        summary="Gatekeeper reviewed the task.",
        error=None,
        exit_code=0,
        awaiting_input=False,
        input_requests=[],
        events=[
            {
                "type": "request.resolved",
                "request_id": "req-1",
                "method": "vibrant.review_task_outcome",
                "result": {"id": "task-001", "status": "accepted"},
            }
        ],
    )

    assert isinstance(payload, GatekeeperRoleResult)
    assert payload.suggested_decision == "accepted"
    assert payload.tool_calls[0].tool_name == "vibrant.review_task_outcome"

    reparsed = parse_role_result(serialize_role_result(payload))
    assert isinstance(reparsed, GatekeeperRoleResult)
    assert reparsed.suggested_decision == "accepted"


def test_gatekeeper_role_result_ignores_failed_tool_calls_for_suggested_decision() -> None:
    payload = build_gatekeeper_role_result(
        summary="Gatekeeper attempted a retry.",
        error=None,
        exit_code=0,
        awaiting_input=False,
        input_requests=[],
        events=[
            {
                "type": "request.resolved",
                "request_id": "req-1",
                "method": "vibrant.mark_task_for_retry",
                "error": {"message": "tool failed"},
            }
        ],
    )

    assert isinstance(payload, GatekeeperRoleResult)
    assert payload.tool_calls[0].status == "failed"
    assert payload.suggested_decision is None

from vibrant.agents.gatekeeper import Gatekeeper, GatekeeperRequest, GatekeeperTrigger, MCP_TOOL_NAMES
from vibrant.project_init import initialize_project


class _NoopAdapter:
    def __init__(self, **kwargs):
        _ = kwargs


def test_gatekeeper_prompt_uses_centralized_template(tmp_path):
    initialize_project(tmp_path)
    skills_dir = tmp_path / ".vibrant" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "api-design.md").write_text(
        "# api-design\nPrefer additive API changes over rewrites.\n",
        encoding="utf-8",
    )

    gatekeeper = Gatekeeper(tmp_path, adapter_factory=_NoopAdapter)
    system_prompt = gatekeeper.render_system_prompt()
    prompt = gatekeeper.render_prompt(
        GatekeeperRequest(
            trigger=GatekeeperTrigger.TASK_COMPLETION,
            trigger_description="Review task-007 output.",
            agent_summary="Implementation is ready for review.",
        )
    )

    assert "You are a long-lived, project-scoped planning and review agent." in system_prompt
    assert "## MCP Tools" in system_prompt
    assert all(tool_name in system_prompt for tool_name in MCP_TOOL_NAMES)
    assert "Use these tools for durable roadmap, workflow, question, and review decisions." in system_prompt
    assert "## Current Consensus" in prompt
    assert "## Current Roadmap" in prompt
    assert "when the MCP bridge is available" not in prompt
    assert "api-design: Prefer additive API changes over rewrites." in prompt
    assert "Implementation is ready for review." in prompt

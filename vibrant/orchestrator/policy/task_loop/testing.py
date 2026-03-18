"""Test-agent invocation helpers."""

from __future__ import annotations

from pathlib import Path

from vibrant.agents.test_agent import PYCUA_SERVER_ID, PYCUA_SUBMODULE_PATH, PYCUA_TOOL_NAME
from vibrant.config import VibrantConfig
from vibrant.providers.invocation import MCPAccessDescriptor, ProviderInvocationPlan
from vibrant.providers.invocation_compiler import compile_provider_invocation


def build_test_agent_invocation_plan(
    *,
    config: VibrantConfig,
    run_id: str,
    role: str = "test",
    extra_access: list[MCPAccessDescriptor] | None = None,
    pycua_enabled: bool = False
) -> ProviderInvocationPlan:
    """Compile invocation plan for a test agent with optional pyCUA MCP access."""

    descriptors = list(extra_access or [])
    if pycua_enabled:
        descriptors.append(
            MCPAccessDescriptor(
                binding_id=f"binding-{role}-{run_id}-pycua",
                role=role,
                run_id=run_id,
                server_id=PYCUA_SERVER_ID,
                transport_hint="stdio",
                stdio_command="uv",
                stdio_args=["run", "--directory", PYCUA_SUBMODULE_PATH, "pycua"],
                required=False,
                visible_tools=[PYCUA_TOOL_NAME],
                metadata={"source": "pyCUA"},
            )
        )
    return compile_provider_invocation(config.provider_kind, descriptors)

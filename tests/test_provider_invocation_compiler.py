from __future__ import annotations

from vibrant.providers.base import ProviderKind
from vibrant.providers.invocation import MCPAccessDescriptor
from vibrant.providers.invocation_compiler import compile_provider_invocation


def test_codex_compiler_renders_loopback_mcp_overrides() -> None:
    descriptor = MCPAccessDescriptor(
        binding_id="binding-gatekeeper-123",
        role="gatekeeper",
        run_id="gatekeeper-123",
        conversation_id="conv-123",
        visible_tools=["vibrant.add_task", "vibrant.update_task_definition"],
        visible_resources=["vibrant.get_consensus"],
        endpoint_url="http://127.0.0.1:8765/mcp",
        server_id="vibrant_gatekeeper_123",
        transport_hint="http",
        static_headers={"X-Vibrant-Binding": "binding-gatekeeper-123"},
    )

    plan = compile_provider_invocation(ProviderKind.CODEX, descriptor)

    assert plan.provider_kind is ProviderKind.CODEX
    assert plan.binding_id == "binding-gatekeeper-123"
    assert plan.visible_tools == ["vibrant.add_task", "vibrant.update_task_definition"]
    assert plan.launch_args == [
        "--config",
        "mcp_servers.vibrant_gatekeeper_123.enabled=true",
        "--config",
        'mcp_servers.vibrant_gatekeeper_123.url="http://127.0.0.1:8765/mcp"',
        "--config",
        "mcp_servers.vibrant_gatekeeper_123.required=true",
        "--config",
        'mcp_servers.vibrant_gatekeeper_123.enabled_tools=["vibrant.add_task", "vibrant.update_task_definition"]',
        "--config",
        'mcp_servers.vibrant_gatekeeper_123.http_headers={ X-Vibrant-Binding = "binding-gatekeeper-123" }',
    ]


def test_codex_compiler_skips_transport_args_without_endpoint() -> None:
    descriptor = MCPAccessDescriptor(
        binding_id="binding-gatekeeper-123",
        role="gatekeeper",
        run_id="gatekeeper-123",
        visible_tools=["vibrant.add_task"],
        visible_resources=["vibrant.get_consensus"],
        server_id="vibrant_gatekeeper_123",
    )

    plan = compile_provider_invocation(ProviderKind.CODEX, descriptor)

    assert plan.launch_args == []
    assert plan.debug_metadata["mcp_transport_ready"] is False


def test_codex_compiler_accepts_multiple_mcp_access_descriptors() -> None:
    descriptors = [
        MCPAccessDescriptor(
            binding_id="binding-gatekeeper-123",
            role="gatekeeper",
            run_id="gatekeeper-123",
            visible_tools=["vibrant.add_task"],
            visible_resources=["vibrant.get_consensus"],
            endpoint_url="http://127.0.0.1:8765/mcp",
            server_id="vibrant_gatekeeper_123",
            transport_hint="http",
            static_headers={"X-Vibrant-Binding": "binding-gatekeeper-123"},
        ),
        MCPAccessDescriptor(
            binding_id="binding-worker-456",
            role="worker",
            run_id="worker-456",
            visible_tools=["vibrant.retry_review_ticket"],
            visible_resources=["vibrant.get_workflow_status"],
            server_id="vibrant_worker_456",
        ),
    ]

    plan = compile_provider_invocation(ProviderKind.CODEX, descriptors)

    assert plan.provider_kind is ProviderKind.CODEX
    assert plan.binding_id is None
    assert plan.visible_tools == ["vibrant.add_task", "vibrant.retry_review_ticket"]
    assert plan.visible_resources == ["vibrant.get_consensus", "vibrant.get_workflow_status"]
    assert plan.launch_args == [
        "--config",
        "mcp_servers.vibrant_gatekeeper_123.enabled=true",
        "--config",
        'mcp_servers.vibrant_gatekeeper_123.url="http://127.0.0.1:8765/mcp"',
        "--config",
        "mcp_servers.vibrant_gatekeeper_123.required=true",
        "--config",
        'mcp_servers.vibrant_gatekeeper_123.enabled_tools=["vibrant.add_task"]',
        "--config",
        'mcp_servers.vibrant_gatekeeper_123.http_headers={ X-Vibrant-Binding = "binding-gatekeeper-123" }',
    ]
    assert isinstance(plan.debug_metadata["mcp_access"], list)
    assert plan.debug_metadata["mcp_transport_ready"] is True
    assert plan.debug_metadata["mcp_ready_bindings"] == ["binding-gatekeeper-123"]
    assert plan.debug_metadata["mcp_pending_bindings"] == ["binding-worker-456"]


def test_non_codex_compiler_accepts_multiple_mcp_access_descriptors() -> None:
    descriptors = [
        MCPAccessDescriptor(
            binding_id="binding-a",
            role="gatekeeper",
            run_id="run-a",
            visible_tools=["vibrant.add_task"],
            visible_resources=["vibrant.get_consensus"],
        ),
        MCPAccessDescriptor(
            binding_id="binding-b",
            role="worker",
            run_id="run-b",
            visible_tools=["vibrant.add_task", "vibrant.retry_review_ticket"],
            visible_resources=["vibrant.get_workflow_status"],
        ),
    ]

    plan = compile_provider_invocation(ProviderKind.CLAUDE, descriptors)

    assert plan.provider_kind is ProviderKind.CLAUDE
    assert plan.binding_id is None
    assert plan.visible_tools == ["vibrant.add_task", "vibrant.retry_review_ticket"]
    assert plan.visible_resources == ["vibrant.get_consensus", "vibrant.get_workflow_status"]
    assert plan.debug_metadata["mcp_runtime_supported"] is False
    assert isinstance(plan.debug_metadata["mcp_access"], list)

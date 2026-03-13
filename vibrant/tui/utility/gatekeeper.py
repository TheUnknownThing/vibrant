from vibrant.orchestrator.facade import OrchestratorFacade, AgentInstanceSnapshot

def get_gatekeeper(facade: OrchestratorFacade) -> AgentInstanceSnapshot:
    candidates = facade.instances.list(role="gatekeeper")
    assert len(candidates) == 1
    return candidates[0]


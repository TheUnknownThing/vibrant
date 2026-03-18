# Vibrant

Next-generation, fully autonomous AI orchestrator for project development and maintenance.

---

## Features

Vibrant is a terminal-based orchestrator designed to manage the lifecycle, state, and execution of autonomous AI agents. Unlike traditional AI coding tools that operate within an IDE or act as chat bots, Vibrant treats agents as scoped workers and provides a robust, durable architecture for planning, implementation, and review.

Vibrant core features include:
- **MCP-based Orchestration**: Vibrant acts as a Model Context Protocol (MCP) to internal agents, allowing for seamless communication and coordination for agents.
- **Consensus as Ground Truth**: Vibrant uses a consensus mechanism to determine the ground truth for the project, ensuring that all agents are aligned and working towards the same goals, and that all goals are visible to the user.
- **Gatekeeper as Project Manager**: Vibrant manages the consensus by a dedicated agent named a **gatekeeper**, which makes technical decisions and guides the project towards completion.
- **Shameless Questioning**: Vibrant encourages agents to ask questions and seek clarification, fostering a collaborative environment where every decision is made with transparency and is trackable.

## Getting Started

First clone the repository and install the dependencies:

```bash
git clone https://github.com/TheUnknownThing/vibrant.git
cd vibrant
uv sync
```

It is advised that you use the `uv tool` command to install Vibrant as a tool locally.

```bash
uv tool install .
```

Then you can run Vibrant in any of your projects files:

```bash
vibrant
```

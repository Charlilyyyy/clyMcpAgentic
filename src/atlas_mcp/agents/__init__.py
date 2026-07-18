"""Support copilot — multi-agent product layer.

Atlas-MCP exposes tools. This package exposes *agents that use those tools*
to solve a concrete enterprise problem end-to-end: customer support triage.

The agents are deliberately simple and explicit. There is no heavy
framework here — just direct LLM calls with clear prompts and a small
orchestrator that runs them in sequence with a bounded critique loop.
This is the pattern that survives production: easy to debug, easy to
change, easy to swap out one agent without rewriting the others.
"""

from atlas_mcp.agents.base import (
    LLM,
    Agent,
    AgentRun,
    LLMProtocol,
    first_text_block,
    parse_json_lenient,
)
from atlas_mcp.agents.critic import CriticAgent, Verdict
from atlas_mcp.agents.mcp_client import AtlasMCPClient, MCPError, ToolResult
from atlas_mcp.agents.orchestrator import CopilotResponse, SupportCopilot
from atlas_mcp.agents.planner import Plan, PlannerAgent, PlanStep
from atlas_mcp.agents.retriever import Finding, RetrievalResult, RetrieverAgent
from atlas_mcp.agents.synthesizer import Draft, SynthesizerAgent

__all__ = [
    "Agent",
    "AgentRun",
    "LLM",
    "LLMProtocol",
    "first_text_block",
    "parse_json_lenient",
    "PlannerAgent",
    "Plan",
    "PlanStep",
    "RetrieverAgent",
    "RetrievalResult",
    "Finding",
    "SynthesizerAgent",
    "Draft",
    "CriticAgent",
    "Verdict",
    "AtlasMCPClient",
    "ToolResult",
    "MCPError",
    "SupportCopilot",
    "CopilotResponse",
]

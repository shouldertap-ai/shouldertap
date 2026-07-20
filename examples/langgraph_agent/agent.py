"""Sketch: a LangGraph node that taps a human expert via ShoulderTap's MCP server when the
agent's own answer confidence is too low. `shouldertap_mcp_client.py` in this directory is the
real, runnable piece (the MCP calls); the LangGraph wiring below is illustrative -- install
`langgraph` and `langchain-core` to actually run this file end to end.

    pip install langgraph langchain-core

The pattern generalizes to any agent framework: wherever you'd normally give up or hedge on a
question retrieval couldn't answer, call `ask_expert(...)` instead. The call returns
immediately with a request id; the human's answer arrives out-of-band once someone replies and
a reviewer accepts it, so a real graph would either poll `check_answer` on a later turn/tick, or
have `ask_expert`'s caller register a webhook consumer and resume the graph from a callback --
polling is simplest to sketch here.
"""

from __future__ import annotations

import asyncio
from typing import TypedDict

from shouldertap_mcp_client import ask_expert, check_answer

CONFIDENCE_THRESHOLD = 0.6


class AgentState(TypedDict):
    question: str
    topic: str
    answer: str | None
    confidence: float
    shouldertap_request_id: str | None


def answer_from_retrieval(state: AgentState) -> AgentState:
    """Stand-in for your own retrieval/RAG step. Replace with the real thing."""
    state["answer"] = "best guess from retrieval"
    state["confidence"] = 0.3  # deliberately low, to trigger the tap below
    return state


def tap_expert_if_unsure(state: AgentState) -> AgentState:
    if state["confidence"] >= CONFIDENCE_THRESHOLD:
        return state

    outcome = asyncio.run(ask_expert(state["question"], state["topic"]))

    if outcome.get("answer") is not None:
        # A dedup hit resolved immediately (spec §4.1) -- no waiting needed.
        state["answer"] = outcome["answer"].get("summary") or str(outcome["answer"])
        state["confidence"] = 1.0
        return state

    state["shouldertap_request_id"] = outcome["request_id"]
    return state


def resume_once_answered(state: AgentState) -> AgentState:
    """Call this on a later turn/tick once a human has plausibly had time to reply."""
    if state["shouldertap_request_id"] is None:
        return state
    result = asyncio.run(check_answer(state["shouldertap_request_id"]))
    if result.get("answer") is not None:
        state["answer"] = result["answer"].get("summary") or str(result["answer"])
        state["confidence"] = 1.0
        state["shouldertap_request_id"] = None
    return state


# --- LangGraph wiring (illustrative -- needs `pip install langgraph`) ---
#
# from langgraph.graph import StateGraph, END
#
# graph = StateGraph(AgentState)
# graph.add_node("retrieve", answer_from_retrieval)
# graph.add_node("tap_expert", tap_expert_if_unsure)
# graph.add_node("resume", resume_once_answered)
# graph.set_entry_point("retrieve")
# graph.add_edge("retrieve", "tap_expert")
# graph.add_edge("tap_expert", END)
# app = graph.compile()


if __name__ == "__main__":
    state: AgentState = {
        "question": "What does 'active customer' mean for Q2 reporting?",
        "topic": "revenue metrics",
        "answer": None,
        "confidence": 0.0,
        "shouldertap_request_id": None,
    }
    state = answer_from_retrieval(state)
    state = tap_expert_if_unsure(state)
    print(state)

# langgraph_agent example

Shows the integration pattern for any AI agent framework: when your own retrieval/answering
step has low confidence, tap a human expert via ShoulderTap's MCP server instead of guessing or
giving up.

- `shouldertap_mcp_client.py` — a small, genuinely runnable async wrapper that spawns
  `shtap mcp` over stdio and calls its `ask_expert` / `check_answer` tools. Try it directly:

  ```bash
  # terminal 1
  shtap serve --transport console

  # terminal 2
  uv run python shouldertap_mcp_client.py "What does 'active customer' mean for Q2 reporting?" "revenue metrics"
  ```

- `agent.py` — sketches where this plugs into a LangGraph node (`tap_expert_if_unsure`). The
  MCP-calling logic is real; the LangGraph wiring at the bottom is commented out and needs
  `pip install langgraph langchain-core` to actually run as a graph. Run the file directly to
  see the tap fire without LangGraph installed:

  ```bash
  uv run python agent.py
  ```

Since `ask_expert` returns immediately with a request id (the human hasn't answered yet), a real
agent needs a resume strategy: poll `check_answer(request_id)` on a later turn (`resume_once_answered`
here), or register a webhook consumer and resume from that callback instead of polling.

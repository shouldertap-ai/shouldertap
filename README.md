# ShoulderTap

When AI agents get stuck, ShoulderTap asks your experts — and writes the answer back so no
agent is ever stuck on it again.

When an agent hits a term it can't resolve, a judgment call, or missing context that no
document answers, ShoulderTap routes the question to the person who knows, asks them on Slack
in a well-formed message, captures their messy human reply into structured knowledge with full
provenance, routes it through human approval, and writes it back to a system of record. The
agent's blocker is resolved, and the organization permanently learns — no agent asks that
question again.

See [OPENCORE.md](OPENCORE.md) for the open-core commitment, [CONTRIBUTING.md](CONTRIBUTING.md)
to get set up for development, and `spec/protocol.md` for the stable, third-party-facing
protocol reference.

## 90-second quickstart (no Slack required)

The `console` transport prints DMs to your terminal and reads replies from stdin — the same
loop as real Slack, with no Slack app or credentials needed for a first look.

```bash
pip install -e .
shtap init                      # writes shouldertap.yaml + .env.example
```

Add an expert to the `experts:` section of `shouldertap.yaml` it just wrote:

```yaml
experts:
  - id: "U1"
    name: "Dana"
    topics: ["revenue metrics"]
```

Then, in one terminal:

```bash
shtap serve --transport console
```

...and in a second terminal:

```bash
shtap ask "What does active customer mean?" --topic "revenue metrics"
```

Terminal 1 prints the DM ShoulderTap would have sent Dana on Slack — self-identification, why
it's asking, the question, and the opt-out. Type a reply there. Terminal 2 is now polling for
an answer; from a third terminal, review and accept it:

```bash
shtap queue                # see the pending proposal
shtap accept <proposal_id>
```

Terminal 2 then prints the accepted answer, attributed to Dana.

## The loop

```
[Consumer/Agent]                [ShoulderTap Engine]                    [Expert on Slack]
      |                                |                                       |
      |--- ContextRequest ------------>|                                       |
      |                                |-- dedup check (skip if duplicate) --- |
      |                                |-- resolve routing (registry) -------- |
      |                                |-- draft question (LLM) -------------->|  DM
      |                                |                                       |
      |                                |<---------- free-text reply -----------|
      |                                |-- structure reply (LLM) ------------- |
      |                                |-- create ContextProposal ------------ |
      |                        [Approval queue: human approves/rejects]        |
      |<-- on_proposal_accepted -------|                                       |
      |    (consumer writes back       |-- notify expert: "your answer         |
      |     to system of record)       |    was accepted, attributed to you" ->|
```

No reply within `escalation_after` (default 2h) re-asks the expert's configured escalation
target. No reply anywhere within `give_up_after` (default 24h) fails the request and notifies
the consumer with `reason: "timeout"`.

## Slack setup

1. Go to <https://api.slack.com/apps> → "Create New App" → "From an app manifest", and paste in
   [`slack/manifest.yaml`](slack/manifest.yaml).
2. Copy the Bot User OAuth Token into `SLACK_BOT_TOKEN` and the Signing Secret into
   `SLACK_SIGNING_SECRET` in your `.env` (copied from `.env.example` — `.env` is gitignored and
   never committed).
3. Point the app's Event Subscriptions request URL at
   `https://<your-host>/api/v1/slack/events`.
4. `shtap serve --transport slack`.

## MCP integration

Any MCP-capable agent can tap a human expert with no SDK integration, via `shtap mcp` (stdio):

```python
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

params = StdioServerParameters(command="shtap", args=["mcp"])
async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
    await session.initialize()
    result = await session.call_tool(
        "ask_expert",
        {"question": "What does 'active customer' mean for Q2 reporting?", "topic": "revenue metrics"},
    )
```

`ask_expert` returns immediately with a request id; the answer arrives out-of-band once a human
replies and a reviewer accepts it — poll with the companion `check_answer(request_id)` tool, or
register a webhook consumer instead. See `examples/langgraph_agent/` for a worked example of
wiring this into an agent graph's low-confidence path.

## The safety promise, to your experts

Every single outbound ask — no exceptions, no code path that skips this — contains, in order:

1. **Self-identification**: "🤝 I'm ShoulderTap, an automated assistant." It never pretends to
   be a person.
2. **Who's asking, and why**: the requesting consumer and why it's stuck.
3. **The question** — one question, drafted to be answerable from memory, no jargon-restating.
4. **Effort framing**: "A one-or-two sentence reply here is all that's needed."
5. **The attribution promise**: your answer is recorded with your name as the source, *after
   human review* — never written back automatically without a person in the loop (unless a
   consumer has explicitly opted into `auto_accept`, which ships off by default specifically
   because skipping review is a real write-back-poisoning risk).
6. **An opt-out**: reply `mute` to stop receiving asks entirely, or `skip` to pass this one
   along to someone else.

Rate limits (`max_open_asks_per_expert`, `max_asks_per_expert_per_day`) and quiet hours are
enforced before every send, not just documented — see `shouldertap.yaml`'s `defaults:` section.

## Learn more

- `spec/protocol.md` — the stable protocol reference (contracts, HTTP API, MCP tools, kind
  schemas) for anyone building a client or an alternative implementation.
- `examples/` — a LangGraph-style agent integration, an OpenMetadata write-back consumer, and
  curl examples for the whole HTTP API.
- `CONTRIBUTING.md` — development setup, DCO sign-off.

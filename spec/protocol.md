# ShoulderTap Protocol Reference

This is the stable, third-party-facing protocol: the wire shapes, HTTP API surface, and MCP
tool interface any ShoulderTap-compatible client or alternative engine implementation needs to
agree on. It's a distilled reference, not the build spec — see the project README and
`CONTRIBUTING.md` for how the reference implementation in this repo is built.

License: this file and everything else under `spec/` is CC-BY-4.0 (see `spec/LICENSE`), not
Apache-2.0 like the code — it's meant to be freely reused by other implementations.

## Contracts

The two core wire shapes are `ContextRequest` (a consumer asking for context) and
`ContextProposal` (an expert's answer, structured and attributed). Canonical JSON Schema for
both — plus `ConsumerRegistration` — lives in `spec/schemas/*.json`, generated directly from
the reference implementation's Pydantic models
(`shouldertap/engine/contracts.py`; regenerate with `scripts/generate_schemas.py`). The field
names and semantics below are normative; the JSON Schema files are the authoritative shape.

### ContextRequest

| field | type | notes |
|---|---|---|
| `id` | string | ULID, engine-assigned if omitted (`req_...`) |
| `org_id` | string | fixed `"default"` in the community edition (single-tenant) |
| `kind` | string | consumer-defined; routes proposals back to the consumers that handle it |
| `topic` | string | routing key; normalized lowercase for matching |
| `question` | string | the raw question |
| `context` | object | free-form, shown to the expert |
| `target_experts` | array\|null | `[{expert_id, role}]`; bypasses topic routing entirely |
| `routing_policy` | object | `primary_experts`, `escalation_targets`, `escalation_after` (ISO-8601 duration, e.g. `"PT2H"`), `give_up_after`, `priority` |
| `consumer` | string | the submitting consumer's id |
| `dedup_key` | string\|null | see Dedup semantics below |
| `correlation` | object\|null | free-form passthrough (e.g. `{"trace_id": "..."}`) |
| `created_at` | string (date-time) | |

### ContextProposal

| field | type | notes |
|---|---|---|
| `id` | string | ULID, engine-assigned (`prop_...`) |
| `request_id` | string | |
| `kind` | string | mirrors the request |
| `answer` | string | the expert's verbatim reply |
| `structured` | object\|null | LLM-structured per the kind's schema; `null` on extraction failure or confidence <0.3 -- the raw answer is never dropped |
| `confidence` | number\|null | the structurer's self-assessed extraction confidence, independent of whether `structured` ended up null |
| `provenance` | object | `expert_id`, `expert_name`, `answered_via`, `slack_thread_ts`, `answered_at`, `escalated` |
| `consumer` | string | |
| `created_at` | string (date-time) | |

### Dedup semantics

A request with a `dedup_key` matching another request from the *same consumer's* dedup window
(default 24h) is deduplicated:

- If the matching request already resolved to an accepted proposal: deliver that proposal to
  the new consumer immediately (`on_proposal_accepted`) — no new ask.
- If the matching request is still open: the new consumer is attached as an additional
  subscriber, fanned out to on resolution — the expert is never asked twice for the same thing.

### Reason codes

`on_request_failed` always carries exactly one of: `timeout`, `no_expert_found`,
`expert_declined`, `rate_limited`, `cancelled`, `error`.

### Consumer registration

`handles_kinds`, `delivery` (a discriminated union: `{"type": "webhook", "url": ...}` or
`{"type": "inprocess"}` for an SDK-embedded consumer), `dedup_window`, `auto_accept` (off by
default — write-back with no human review is a real poisoning risk, so this is an explicit,
documented opt-in, not a default), and optional per-kind custom capture schemas.

### Callbacks

A registered consumer receives up to four events, whether delivered as an in-process callable
or a webhook POST (`{"event": "<name>", ...}`):

- `on_proposal(proposal)` — informational, fires pre-approval.
- `on_proposal_accepted(proposal)` — the actual delivery event; write your system of record here.
- `on_proposal_rejected(proposal, reason)`
- `on_request_failed(request_id, reason)`

## HTTP API (`/api/v1`)

Static bearer token auth (`Authorization: Bearer <token>`) on every route except
`/slack/events`, which Slack signs itself. See `examples/curl.md` for worked examples of every
endpoint below.

| method | path | |
|---|---|---|
| POST | `/requests` | submit a `ContextRequest`; returns `{id, status}` |
| GET | `/requests/{id}` | full status + accepted proposal if resolved |
| POST | `/consumers` | register (webhook delivery) |
| DELETE | `/consumers/{id}` | unregister |
| GET | `/proposals?status=pending` | the approval queue |
| POST | `/proposals/{id}/accept` | `{decided_by, note?}` |
| POST | `/proposals/{id}/reject` | `{decided_by, reason}` |
| GET | `/experts` | registry + live state (muted, open_asks, asks_today) |
| PUT | `/experts` | full replace; rewrites the config file, which remains the source of truth |
| GET | `/audit?request_id=` | the full trail for one tap |
| POST | `/slack/events` | Slack Events API endpoint |

Request statuses progress through: `queued → asked → escalated → proposed → accepted|rejected|failed`.

## MCP tools

`ask_expert` and `check_answer`, exposed by `shtap mcp` over stdio.

```
ask_expert(question: str, topic: str, kind: str = "freeform.answer",
           context: object? = null, dedup_key: str? = null)
  -> { request_id: str, status: str, answer?: object }
```

`answer` is present only when the call immediately resolved via a dedup hit against an already-
accepted proposal; otherwise the answer arrives out-of-band and the caller polls or waits for
its own consumer callback.

```
check_answer(request_id: str) -> { status: str, answer?: object }
```

## Kind schemas

Two kinds ship built in (`spec/schemas/kinds/*.json`, generated from
`shouldertap/engine/capture.py`'s `BUILTIN_KIND_SCHEMAS`):

- `glossary.definition` — `{definition: str, caveats: [str], examples: [str]?}`
- `freeform.answer` — `{summary: str, details: str?}`

Consumers may register their own kind with a custom JSON Schema at registration time; a custom
schema for a kind takes precedence over a built-in one of the same name.

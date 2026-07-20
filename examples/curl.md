# curl examples

Assumes `shtap serve` is running on the default port (`:8776`) and `$SHOULDERTAP_API_TOKEN` is
set in your shell to the value from your `.env`.

## Submit a request (as an external agent/consumer)

```bash
curl -X POST http://localhost:8776/api/v1/requests \
  -H "Authorization: Bearer $SHOULDERTAP_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "kind": "glossary.definition",
    "topic": "revenue metrics",
    "question": "What does '\''active customer'\'' mean for Q2 reporting?",
    "consumer": "bi.assistant",
    "context": {
      "asked_because": "BI agent hit low confidence answering a user query",
      "entity": "dim_customers.active_flag"
    },
    "dedup_key": "glossary:dim_customers.active_flag"
  }'
# => {"id": "req_01J...", "status": "queued"}
```

## Check a request's status and (once accepted) its answer

```bash
curl http://localhost:8776/api/v1/requests/req_01J... \
  -H "Authorization: Bearer $SHOULDERTAP_API_TOKEN"
```

## List proposals pending human review

```bash
curl "http://localhost:8776/api/v1/proposals?status=pending" \
  -H "Authorization: Bearer $SHOULDERTAP_API_TOKEN"
```

## Accept / reject a proposal

```bash
curl -X POST http://localhost:8776/api/v1/proposals/prop_01J.../accept \
  -H "Authorization: Bearer $SHOULDERTAP_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"decided_by": "alice"}'

curl -X POST http://localhost:8776/api/v1/proposals/prop_01J.../reject \
  -H "Authorization: Bearer $SHOULDERTAP_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"decided_by": "alice", "reason": "not accurate"}'
```

## Register a webhook consumer

```bash
curl -X POST http://localhost:8776/api/v1/consumers \
  -H "Authorization: Bearer $SHOULDERTAP_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "my-consumer",
    "handles_kinds": ["glossary.definition", "freeform.answer"],
    "delivery": {"type": "webhook", "url": "https://my-service.example.com/shouldertap-webhook"},
    "dedup_window": "PT24H"
  }'
```

Your webhook receives a POST for each event with a body shaped like
`{"event": "proposal_accepted", "proposal": {...ContextProposal...}}` (also `"proposal"` for
the pre-approval `on_proposal` event, `"reason"` alongside `"proposal"` for
`proposal_rejected`, and `"request_id"`/`"reason"` for `request_failed`).

## Read/replace the expert registry

```bash
curl http://localhost:8776/api/v1/experts -H "Authorization: Bearer $SHOULDERTAP_API_TOKEN"

curl -X PUT http://localhost:8776/api/v1/experts \
  -H "Authorization: Bearer $SHOULDERTAP_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '[{"id": "U0123ABC", "name": "Dana Kim", "topics": ["revenue metrics"], "escalation_to": "U0456DEF"}]'
```

Note: `PUT /experts` rewrites `shouldertap.yaml` itself (the config file remains the source of
truth) -- this is a full replace, not a merge.

## Full audit trail for one tap

```bash
curl "http://localhost:8776/api/v1/audit?request_id=req_01J..." \
  -H "Authorization: Bearer $SHOULDERTAP_API_TOKEN"
```

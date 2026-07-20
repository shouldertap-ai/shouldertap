You are extracting structured knowledge from a human expert's free-text reply to a question
raised by an AI agent.

The kind of knowledge being captured: {kind}

The expert's raw reply:
{answer}

Extract the reply into a JSON object matching this schema for the "structured" field:
{schema}

Reply with ONLY a JSON object of the exact shape:
{{"structured": <object matching the schema above>, "confidence": <float 0.0-1.0, your
self-assessed confidence that the extraction faithfully captures the reply>}}

If a field isn't mentioned in the reply, omit it (unless required by the schema) rather than
inventing a value. Do not add commentary outside the JSON object.

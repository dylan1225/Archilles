# Deep Agents implementation

Requested branch: `deep-agent-implementation`. Creation is blocked by read-only Git metadata in this session; existing working changes are preserved.

## Design
- Use `nvidia/nemotron-3.5-super-vl:free` through OpenRouter.
- Fast path: one concise structured vision extraction, then a Deep Agent coordinator delegates to two specialist subagents (minimal-change and geometry/material design).
- Specialists submit candidates through deterministic tools. Tools attach mechanics/reference data and check explicit constraints. Coordinator ranks submitted candidate IDs, never fabricates calculation output.
- Request both specialists in the same delegation round; bound model tokens, graph recursion, total wall time and candidate count. Actual concurrency and latency must be verified with installed dependencies and live API access.
- Stream actual extraction, delegation, tool and completion events. Keep the existing Gemini mode and all saved examples usable.
- Allow redesign of an existing result without another vision call. Follow-ups carry the previous recommendations and explicitly retained constraints.
- Do not apply the notched-bar fillet table to arbitrary brackets. Missing/estimated dimensions and unsupported weight changes remain unverified.

## Verification
- Offline tests: candidate math, constraint rejection, provenance, invalid IDs, insufficient photo, timeouts, missing dependencies and isolated request state.
- Live benchmark: identical image/note/material, cache bypassed, alternating Gemini and agent runs, total time and first useful result reported. Do not claim a speedup without successful paired measurements.
- Browser: saved examples, engine availability, redesign input, progress/error behavior, existing results preserved after agent failure.

## Environment blockers
- Git metadata is read-only: cannot create or push the branch here.
- Package registry / model network access unavailable from the execution shell: dependencies and live model behavior cannot yet be verified.

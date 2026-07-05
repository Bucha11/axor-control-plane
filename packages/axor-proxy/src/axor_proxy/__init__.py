"""axor-proxy: passthrough proxy with fault injection and observation.

Spec section 6 rules, in order of importance:
- Auth is passthrough, byte-for-byte. The proxy never parses, substitutes,
  or stores credentials.
- Exactly two intervention points: inject fault (scenario), record observation.
- Observe-only: never blocks the agent.
- No raw bodies persisted by default (persist_inputs=False mirror).
"""

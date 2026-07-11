# Recipes — putting Axor in front of real agent frameworks

Two integration depths (see the depth ladder):

- **proxy (observe-only, no code change)**: point the framework's tool base
  URLs at `http://<proxy>/t/<tool>/`. Auth passes through byte-for-byte; every
  call is observed; armed faults inject. These recipes show exactly that line.
- **adapter (full governance)**: wrap the agent in an axor-core Invokable —
  see `axor-core`'s README; that's what unlocks live Control.

Honesty note: these recipes are complete and runnable, but exercising them
end-to-end needs the framework installed AND an LLM API key, so they are not
part of this repo's CI. The framework-free equivalent (the scripted agent)
runs in CI on every push.

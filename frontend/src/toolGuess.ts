// What a tool does, guessed from its name — for tools the plane has seen an
// agent call but nobody has described yet (Replay's "make it a rule", Policy's
// "start from the tools an agent used"). The same reading the backend's step
// search uses (step_search.py); the person corrects it in Policy.
import type { FoundStep } from "./api";
import type { ToolKlass } from "./store";

const RUNS_CODE = new Set(["bash", "shell", "exec", "run_shell", "execute", "terminal", "run_command",
  "local_shell", "python", "code_interpreter", "exec_command"]);
const WRITES = new Set(["write", "edit", "multiedit", "apply_patch", "notebookedit", "write_file",
  "create_file", "edit_file", "delete_file", "str_replace_editor"]);
const SENDS_OUT = /post|send|email|mail|slack|webhook|upload|publish|tweet|message|notify|http_request|reply/i;

export function guessKlass(tool: string): ToolKlass {
  const t = tool.toLowerCase();
  if (RUNS_CODE.has(t)) return "EXEC";
  if (WRITES.has(t)) return "WRITE";
  if (SENDS_OUT.test(t)) return "EXPORT";
  return "READ";
}

/** Every tool in the found steps, with its guessed kind and whether any of its
 * answers was outside text. */
export function toolsFromSteps(steps: FoundStep[]): Record<string, { klass: ToolKlass; outside: boolean }> {
  const out: Record<string, { klass: ToolKlass; outside: boolean }> = {};
  for (const s of steps) {
    if (!s.tool) continue;
    const t = (out[s.tool] ??= { klass: guessKlass(s.tool), outside: false });
    if (s.outcome === "outside") t.outside = true;
  }
  return out;
}

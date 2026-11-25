// The operators' public keys, put into `axor-wrap connect` so a machine joins
// in the signed posture with no step of its own: what widens for its agents
// (the team's rules, team skills, notes) is then taken only when an operator
// signed it. Trust on first use — the fingerprint is there to compare with
// what `axor-wrap connect` prints, and with one an admin shared.
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export function useOperatorKeys(): { flags: string; fingerprint: string; count: number } {
  const q = useQuery({ queryKey: ["operator-keys"], queryFn: api.operatorKeys, retry: false,
                       staleTime: 60_000 });
  const keys = q.data?.keys ?? {};
  const ops = Object.keys(keys).sort();
  return {
    flags: ops.map((op) => ` --operator-key ${op}=${keys[op]}`).join(""),
    fingerprint: q.data?.fingerprint ?? "",
    count: ops.length,
  };
}

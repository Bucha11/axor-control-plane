// Whether this workspace has a team: someone besides the person signed in.
// Pairing is watching a teammate's agent — alone, there is nobody to pair
// with (people from outside the team are the paid plan), so the screens do not
// offer it. An open plane, a guest, or one still loading: offered (examples).
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { useApp } from "./store";

export function useHasTeam(): boolean {
  const email = useApp((s) => s.identityEmail);
  const me = useQuery({ queryKey: ["identity-me"], queryFn: api.identityMe, enabled: !!email, retry: false });
  const org = me.data?.active_org ?? "";
  const members = useQuery({ queryKey: ["org-members", org], queryFn: () => api.orgMembers(org),
                             enabled: !!org, retry: false, staleTime: 60_000 });
  if (!email || !members.data) return true;
  return members.data.members.length > 1;
}

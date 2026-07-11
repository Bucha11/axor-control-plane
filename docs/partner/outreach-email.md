# Outreach templates — design partner sourcing

Three sources (see ../design-partner-kit.md): orgs adjacent to the paper,
HN-launch inbound, MCP-community teams. Rules that keep reply rates honest:
under 120 words, one concrete claim, one ask, video link only after the
dress-rehearsal footage exists.

## Cold (they run agents in prod)

> **Subject:** 3 design partner slots — runtime governance for LLM agents
>
> Hi [NAME] — saw [SPECIFIC: your post about running N agents / your MCP
> server / your talk on X].
>
> We catch the moment an LLM agent fabricates a result after a tool failure
> and produce a signed, audit-ready receipt of it — plus a live kill switch
> (pause / budget-cap / cascade-stop). Fully self-hosted; your traffic never
> leaves your infra.
>
> We're taking 3 design partners: Team tier free for a year, direct roadmap
> influence, hands-on integration help. In return: an hour every two weeks
> and permission to cite one agreed metric after your approval.
>
> Worth 20 minutes this week? [VIDEO LINK] · [SITE LINK]

## Warm (HN / launch inbound reply)

> Thanks for the comment/DM. Quick context question: how many agents do you
> run in production today, and who owns their reliability?
>
> Asking because we're picking 3 design partners this quarter (Team free for
> 12 months, roadmap influence, integration help). If you have 5+ agents in
> prod and a platform owner, you'd qualify — happy to walk you through a
> caught-fabrication live in 20 minutes.

## Follow-up (one only, +5 business days)

> One-line follow-up: the 3 partner slots are one-per-vertical and
> [VERTICAL] is still open. If the timing's wrong, a "not now" is a perfectly
> good answer and I'll leave you alone.

## Qualification call checklist (20 min)

1. ≥5 agents in prod, or a committed quarter plan? (fewer = waitlist)
2. Named champion on platform/SRE side — a person, not "the team"?
3. Can prod or prod-shadow traffic flow through the proxy within 30 days?
4. Vertical slot still open?
5. Data sensitivity → confirm self-hosted works for them (it almost always
   does; nothing leaves their infra — have SECURITY.md ready).

Two misses → waitlist, politely. Scarcity is real, not theatre.

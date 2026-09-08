"""The EvidenceCase leaving the product (spec §8.3).

Export and share are the only surfaces where the primary artifact crosses out
of the workspace — one of them behind an unauthenticated public URL. Three
things must hold on the way out: the link is exactly as alive as its row says,
the receipt carries the whole case, and whatever the content rule removed is
named rather than silently gone.
"""
from __future__ import annotations

import pathlib
import re

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.lifecycle import prune_once
from axor_backend.share import (
    _BOTTOM,
    _LEADING,
    _LINES_PER_PAGE,
    _TOP,
    evidence_receipt_html,
    evidence_receipt_pdf,
)

CASE = {
    "scenario": "exfil", "deviation": "tainted_value_exfiltrated",
    "verdict_source": "deterministic", "confidence": 1.0,
    "observed_reality": {"tool": "slack_post", "driving_value": "v_sum"},
    "agent_claim": "posted the summary", "fault_attribution": [],
}


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/share.db",
        operator_keys={}, allow_unsigned=True, retention_days=1,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c, app.router.lifespan_context(app):
        c._app = app  # type: ignore[attr-defined]
        yield c


def _pdf_pages(pdf: bytes) -> list[list[str]]:
    """The text lines of each page, straight out of the content streams."""
    body = pdf.decode("latin-1")
    return [
        re.findall(r"\((.*?)\) Tj", stream)
        for stream in re.findall(r"stream\n(.*?)\nendstream", body, re.S)
    ]


# ── the link is as alive as its row ───────────────────────────────────────────

class TestAShareLinkLivesOnlyInItsRow:
    """There used to be an in-process index of share links, rehydrated at boot
    and authoritative for the public resolve route, while the table was
    authoritative for everything else."""

    async def test_a_pruned_link_does_not_come_back_with_a_reused_run_id(
        self, client: httpx.AsyncClient,
    ) -> None:
        """Retention deletes a pruned run's links from the table. The index
        kept them, so the moment the run id was used again — ids are
        client-chosen and `upsert_run` exists to reuse them — the old public
        token served the NEW case."""
        store = client._app.state.store  # type: ignore[attr-defined]
        await store.upsert_run("reused", "n1", "old", "2020-01-01T00:00:00+00:00")
        await store.set_evidence("reused", [
            {**CASE, "observed_reality": {"marker": "OLD-RUN-SECRET"}},
        ])
        token = (await client.post("/v1/runs/reused/cases/0/share")).json()["token"]
        assert "OLD-RUN-SECRET" in (await client.get(f"/v1/share/{token}")).text

        await prune_once(client._app.state)  # type: ignore[attr-defined]
        assert await store.get_share_link(token) is None
        assert (await client.get(f"/v1/share/{token}")).status_code == 404

        await store.upsert_run("reused", "n1", "new", "2026-09-08T00:00:00+00:00")
        await store.set_evidence("reused", [
            {**CASE, "observed_reality": {"marker": "NEW-RUN-SECRET"}},
        ])
        resurrected = await client.get(f"/v1/share/{token}")
        assert resurrected.status_code == 404
        assert "NEW-RUN-SECRET" not in resurrected.text

    async def test_a_revoke_written_elsewhere_takes_effect_now(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The row is read on every resolve, so a revoke does not wait for a
        restart to be believed — which is what an index rebuilt at boot meant.
        """
        store = client._app.state.store  # type: ignore[attr-defined]
        await store.upsert_run("r1", "n1", "s", "2026-09-08T00:00:00+00:00")
        await store.set_evidence("r1", [CASE])
        token = (await client.post("/v1/runs/r1/cases/0/share")).json()["token"]
        assert (await client.get(f"/v1/share/{token}")).status_code == 200

        await store.revoke_share_link(token)  # as retention or another writer would
        assert (await client.get(f"/v1/share/{token}")).status_code == 404

    async def test_revoke_is_the_org_scoped_update_and_nothing_else(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The route no longer consults an index first: the UPDATE's own reach
        decides. A miss is "no such token" or "not yours" and both answer 404;
        a repeat is a hit, so DELETE stays idempotent. The cross-tenant half is
        test_tenant_isolation.test_share_link_resolves_for_an_identity_org."""
        store = client._app.state.store  # type: ignore[attr-defined]
        await store.upsert_run("r2", "n1", "s", "2026-09-08T00:00:00+00:00")
        await store.set_evidence("r2", [CASE])
        token = (await client.post("/v1/runs/r2/cases/0/share")).json()["token"]
        assert (await client.delete("/v1/share/nonsense")).status_code == 404
        assert (await client.delete(f"/v1/share/{token}")).status_code == 200
        assert (await client.get(f"/v1/share/{token}")).status_code == 404
        assert (await client.delete(f"/v1/share/{token}")).status_code == 200


# ── the receipt carries the whole case ────────────────────────────────────────

def _long_case() -> dict:
    return {
        **CASE,
        "observed_reality": {f"step_{i}": f"observation {i}" for i in range(40)},
        "agent_claim": "\n".join(f"claim line {i}" for i in range(20)),
        "fault_attribution": [
            {"fault_mode": f"m{i}", "tool_name": f"t{i}", "influence": "strong"}
            for i in range(10)
        ],
    }


class TestThePdfReceiptDoesNotRunOffThePage:
    """One page, no pagination: text started at the top margin and stepped down
    by the leading until it was writing below a MediaBox 792pt tall. The file
    stayed valid — `%PDF-1.4` … `%%EOF`, which is all the suite checked — and
    on a 40-observation case it silently lost the whole claim, the fault
    attribution, and the line that states the content rule."""

    def test_a_long_case_paginates(self) -> None:
        pages = _pdf_pages(evidence_receipt_pdf("run", _long_case(), "demo"))
        assert len(pages) > 1
        flat = [line for page in pages for line in page]
        assert any("claim line 19" == line for line in flat)
        assert any("observations only" in line for line in flat)
        assert any("m9 on t9" in line for line in flat)

    def test_no_line_is_written_below_the_page(self) -> None:
        pages = _pdf_pages(evidence_receipt_pdf("run", _long_case(), "demo"))
        for page in pages:
            assert len(page) <= _LINES_PER_PAGE
            assert _TOP - _LEADING * (len(page) - 1) >= _BOTTOM

    def test_the_page_tree_matches_the_pages(self) -> None:
        pdf = evidence_receipt_pdf("run", _long_case(), "demo")
        pages = _pdf_pages(pdf)
        assert int(re.search(rb"/Count (\d+)", pdf).group(1)) == len(pages)
        assert pdf.count(b"/MediaBox") == len(pages)
        assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")

    def test_a_short_case_stays_one_page_with_no_page_footer(self) -> None:
        pages = _pdf_pages(evidence_receipt_pdf("run", CASE, "demo"))
        assert len(pages) == 1
        assert not any("page 1 of" in line for line in pages[0])


class TestThePdfSaysWhatItCannotDraw:
    """`encode("latin-1", "replace")` turned every non-Latin-1 character into a
    bare "?" — a Cyrillic receipt was rows of question marks, and the em dash in
    this module's own boilerplate was mangled in every PDF ever produced."""

    CYRILLIC = {
        **CASE,
        "observed_reality": "агент вызвал web_search — пустой ответ",
        "agent_claim": "Согласно результатам, ставки выросли",
    }

    def test_unrenderable_characters_are_marked_and_counted(self) -> None:
        pdf = evidence_receipt_pdf("run", self.CYRILLIC, "demo")
        flat = [line for page in _pdf_pages(pdf) for line in page]
        assert any("[?]" in line for line in flat)
        note = next(line for line in flat if line.startswith("NOTE:"))
        assert re.match(r"NOTE: \d+ character", note)
        assert any("HTML export" in line for line in flat)

    def test_winansi_carries_what_latin_1_replace_used_to_mangle(self) -> None:
        pdf = evidence_receipt_pdf("run", self.CYRILLIC, "demo")
        assert b"/WinAnsiEncoding" in pdf
        assert b"\x97" in pdf  # the em dash, drawn rather than replaced by '?'

    def test_a_latin_only_case_carries_no_note(self) -> None:
        flat = [line for page in _pdf_pages(evidence_receipt_pdf("run", CASE, "s"))
                for line in page]
        assert not any(line.startswith("NOTE:") for line in flat)
        assert not any("[?]" in line for line in flat)

    def test_the_html_receipt_keeps_the_text_intact(self) -> None:
        page = evidence_receipt_html("run", self.CYRILLIC, "demo")
        assert "ставки выросли" in page


# ── what the content rule removed is named ────────────────────────────────────

class TestTheContentRuleNamesWhatItWithheld:
    """The removal was invisible: a shared receipt gave its reader no way to
    tell a case that carried nothing from a case something was taken out of."""

    LEAKY = {
        **CASE,
        "observed_reality": {
            "tool": "web_search",
            "response_body": "SECRET-BODY",
            "headers": {"Authorization": "Bearer SECRET-TOKEN"},
        },
    }

    def test_the_values_never_reach_the_export(self) -> None:
        page = evidence_receipt_html("run", self.LEAKY, "s")
        assert "SECRET-BODY" not in page and "SECRET-TOKEN" not in page

    def test_the_paths_do(self) -> None:
        page = evidence_receipt_html("run", self.LEAKY, "s")
        assert "observed_reality.response_body" in page
        # matched case-insensitively: a header is `Authorization`, not `authorization`
        assert "observed_reality.headers.Authorization" in page

    def test_the_pdf_says_it_too(self) -> None:
        flat = [line for page in _pdf_pages(evidence_receipt_pdf("run", self.LEAKY, "s"))
                for line in page]
        assert any("withheld by the export content rule" in line for line in flat)
        assert not any("SECRET" in line for line in flat)

    def test_a_clean_case_claims_nothing_was_withheld(self) -> None:
        assert "withheld by the export content rule" not in evidence_receipt_html(
            "run", CASE, "s"
        )

    async def test_a_shared_link_carries_the_same_notice(
        self, client: httpx.AsyncClient,
    ) -> None:
        store = client._app.state.store  # type: ignore[attr-defined]
        await store.upsert_run("r3", "n1", "s", "2026-09-08T00:00:00+00:00")
        await store.set_evidence("r3", [self.LEAKY])
        token = (await client.post("/v1/runs/r3/cases/0/share")).json()["token"]
        page = (await client.get(f"/v1/share/{token}")).text
        assert "SECRET-BODY" not in page
        assert "observed_reality.response_body" in page

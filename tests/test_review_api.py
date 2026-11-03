"""Human review: POST /mentions/{run_id}/review, the review queue, run detail and highlights."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
import psycopg
import pytest
from sqlalchemy import func, select, text

from app.audit.pg_repo import PgAuditRepo
from app.audit.records import RunRecord, StepKey
from app.db.models import Audit, Review
from app.db.models import SourceEvidence as EvidenceRow
from app.schemas import Citation, Mention, Verdict
from app.verify.verifier import locate_quote
from tests.api_support import ApiHarness, build_harness
from tests.conftest import run_async

Conn = psycopg.Connection[Any]
KEY = {"X-API-Key": "test-key"}
RAW = "Acme is great for startups. Rival is also popular with teams."
T = datetime(2026, 11, 3, tzinfo=UTC)


def scenario(url: str, body: Callable[[ApiHarness, httpx.AsyncClient], Awaitable[None]]) -> None:
    async def go() -> None:
        h = build_harness(url)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=h.app), base_url="http://t"
        ) as c:
            try:
                await body(h, c)
            finally:
                await h.close()

    run_async(go)


async def seed_audit(h: ApiHarness, brand: str = "Acme", status: str = "complete") -> str:
    async with h.session_factory() as s, s.begin():
        audit = Audit(
            idempotency_key=f"seed-{uuid.uuid4()}",
            request={
                "brand": brand,
                "category": "crm",
                "questions": ["Q?"],
                "competitors": ["Rival"],
            },
            brand=brand,
            status=status,
        )
        s.add(audit)
        await s.flush()
        return str(audit.id)


async def seed_run(
    h: ApiHarness,
    audit_id: str,
    *,
    q: int = 0,
    repeat: int = 0,
    mentioned: bool = True,
    verified: bool = False,
    position: int | None = 1,
    status: Literal["ok", "failed", "pending"] = "ok",
    competitors: list[str] | None = None,
    urls: tuple[str, ...] = (),
    raw: str = RAW,
) -> str:
    key = StepKey(
        question_idx=q, provider="gemini", model="g", repeat_idx=repeat, search_mode="grounded"
    )
    cites = [
        Citation(
            url=u,
            domain=u.split("//")[1].split("/")[0],
            title="T",
            source="grounding_metadata",
            retrieved_at=T,
        )
        for u in urls
    ]
    ok = status == "ok"
    record = RunRecord(
        key=key,
        question=f"Question {q}?",
        status=status,
        raw_answer=raw if status != "failed" else None,
        raw_grounding={"x": 1} if ok else None,
        citations=cites,
        mention=Mention(
            brand_mentioned=mentioned,
            position=position if mentioned else None,
            quote="Acme is great for startups." if mentioned else None,
            competitors_seen=competitors or [],
            sentiment="pos" if mentioned else "neu",
        )
        if ok
        else None,
        verdict=Verdict(status="verified" if verified else "flagged", reason="because")
        if ok
        else None,
        latency_ms=100 if ok else None,
        error=None if ok else "boom",
    )
    await PgAuditRepo(h.session_factory).save_run(audit_id, record)
    return record.id


def post(c: httpx.AsyncClient, run_id: str, **body: Any) -> Any:
    payload = {"action": "approve", "reason": "checked the answer", **body}
    return c.post(f"/mentions/{run_id}/review", json=payload, headers=KEY)


async def audit_summary(c: httpx.AsyncClient, audit_id: str) -> dict[str, Any]:
    return (await c.get(f"/audits/{audit_id}", headers=KEY)).json()["summary"]  # type: ignore[no-any-return]


# --- POST /mentions/{id}/review ---------------------------------------------------------------------------


def test_approving_a_flagged_mention_counts_it_and_recomputes_the_summary(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await seed_audit(h)
        run_id = await seed_run(h, aid, mentioned=True, verified=False)
        before = await audit_summary(c, aid)
        assert before["mention_rate"] == 0.0 and before["flagged_count"] == 1
        r = await post(c, run_id, action="approve", reason="quote is fine, punctuation differs")
        assert r.status_code == 201
        data = r.json()
        assert data["run_id"] == run_id and data["audit_id"] == aid and data["action"] == "approve"
        assert data["reason"] == "quote is fine, punctuation differs"
        assert data["reviewer"] and data["corrected_value"] is None
        assert data["summary"]["mention_rate"] == 1.0 and data["summary"]["flagged_count"] == 0
        assert data["summary"] == await audit_summary(
            c, aid
        )  # the same numbers GET /audits/{id} serves

    scenario(migrated_db_url, body)


def test_rejecting_a_verified_mention_removes_it(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await seed_audit(h)
        run_id = await seed_run(h, aid, mentioned=True, verified=True)
        assert (await audit_summary(c, aid))["mention_rate"] == 1.0
        r = await post(c, run_id, action="reject", reason="the quote is about a different Acme")
        assert r.json()["summary"]["mention_rate"] == 0.0

    scenario(migrated_db_url, body)


@pytest.mark.parametrize(
    ("mentioned", "verified", "value", "expected_rate"),
    [
        (True, True, "not_mentioned", 0.0),
        (False, True, "mentioned", 1.0),
        (True, False, "mentioned", 1.0),
    ],
)
def test_correcting_the_mention_outcome(
    migrated_db_url: str,
    db: Conn,
    mentioned: bool,
    verified: bool,
    value: str,
    expected_rate: float,
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await seed_audit(h)
        run_id = await seed_run(h, aid, mentioned=mentioned, verified=verified)
        r = await post(
            c, run_id, action="correct", corrected_value=value, reason="re-read the answer"
        )
        assert r.status_code == 201 and r.json()["corrected_value"] == value
        assert r.json()["summary"]["mention_rate"] == expected_rate

    scenario(migrated_db_url, body)


def test_correcting_the_position_changes_the_average_position(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await seed_audit(h)
        run_id = await seed_run(h, aid, mentioned=True, verified=True, position=1)
        assert (await audit_summary(c, aid))["avg_position"] == 1.0
        r = await post(
            c,
            run_id,
            action="correct",
            corrected_value="position=3",
            reason="Rival and Globex come first",
        )
        assert r.json()["summary"]["avg_position"] == 3.0
        assert r.json()["summary"]["mention_rate"] == 1.0  # still a confirmed mention

    scenario(migrated_db_url, body)


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "correct", "reason": "x"},  # no corrected_value
        {"action": "correct", "corrected_value": "maybe", "reason": "x"},
        {"action": "correct", "corrected_value": "position=0", "reason": "x"},
        {"action": "correct", "corrected_value": "position=abc", "reason": "x"},
        {
            "action": "approve",
            "corrected_value": "mentioned",
            "reason": "x",
        },  # only valid with correct
        {"action": "reject", "corrected_value": "mentioned", "reason": "x"},
        {"action": "approve", "reason": ""},
        {"action": "approve", "reason": "   "},
        {"action": "approve"},
        {"action": "accept", "reason": "x"},  # renamed to approve
        {"action": "delete", "reason": "x"},
        {
            "action": "approve",
            "reason": "x",
            "reviewer": "someone-else",
        },  # identity is not client-supplied
        {"reason": "x"},
    ],
)
def test_invalid_review_requests_are_422_and_store_nothing(
    migrated_db_url: str, db: Conn, payload: dict[str, Any]
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await seed_audit(h)
        run_id = await seed_run(h, aid)
        r = await c.post(f"/mentions/{run_id}/review", json=payload, headers=KEY)
        assert r.status_code == 422
        async with h.session_factory() as s:
            assert (await s.execute(select(func.count()).select_from(Review))).scalar_one() == 0

    scenario(migrated_db_url, body)


def test_unknown_runs_are_404_and_a_key_is_required(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        assert (await post(c, str(uuid.uuid4()))).status_code == 404
        assert (await post(c, "not-a-uuid")).status_code == 404
        aid = await seed_audit(h)
        run_id = await seed_run(h, aid)
        no_key = await c.post(
            f"/mentions/{run_id}/review", json={"action": "approve", "reason": "x"}
        )
        assert no_key.status_code == 401

    scenario(migrated_db_url, body)


@pytest.mark.parametrize("status", ["failed", "pending"])
def test_a_run_without_an_extracted_mention_cannot_be_reviewed(
    migrated_db_url: str, db: Conn, status: str
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await seed_audit(h)
        run_id = await seed_run(h, aid, status=status)  # type: ignore[arg-type]
        r = await post(c, run_id)
        assert r.status_code == 409 and "mention" in r.json()["detail"]

    scenario(migrated_db_url, body)


def test_the_original_output_is_never_overwritten(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await seed_audit(h)
        run_id = await seed_run(h, aid, mentioned=True, verified=False)
        repo = PgAuditRepo(h.session_factory)
        before = [r for r in await repo.list_runs(aid) if r.id == run_id][0]
        await post(
            c, run_id, action="correct", corrected_value="not_mentioned", reason="wrong brand"
        )
        after = [r for r in await repo.list_runs(aid) if r.id == run_id][0]
        assert after.raw_answer == before.raw_answer and after.raw_grounding == before.raw_grounding
        assert after.mention == before.mention and after.verdict == before.verdict  # untouched
        assert (
            after.review is not None and after.review.action == "correct"
        )  # the review sits beside it

    scenario(migrated_db_url, body)


def test_reviewer_and_timestamp_are_set_by_the_server_and_stored(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await seed_audit(h)
        run_id = await seed_run(h, aid)
        started = datetime.now(UTC) - timedelta(seconds=2)
        data = (await post(c, run_id, reason="looked right")).json()
        stamp = datetime.fromisoformat(data["timestamp"])
        assert started <= stamp <= datetime.now(UTC) + timedelta(seconds=2)
        async with h.session_factory() as s:
            row = (await s.execute(select(Review))).scalar_one()
            assert (row.reviewer, row.reason, row.action) == (
                data["reviewer"],
                "looked right",
                "approve",
            )
            assert str(row.run_id) == run_id and row.timestamp == stamp

    scenario(migrated_db_url, body)


def test_reviews_are_append_only_and_the_latest_one_is_effective(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await seed_audit(h)
        run_id = await seed_run(h, aid, mentioned=True, verified=False)
        assert (await post(c, run_id, action="approve")).json()["summary"]["mention_rate"] == 1.0
        assert (await post(c, run_id, action="reject", reason="changed my mind")).json()["summary"][
            "mention_rate"
        ] == 0.0
        async with h.session_factory() as s:
            assert (await s.execute(select(func.count()).select_from(Review))).scalar_one() == 2
        with pytest.raises(Exception, match="append-only"):
            async with h.session_factory() as s, s.begin():
                await s.execute(text("UPDATE reviews SET reason = 'edited'"))

    scenario(migrated_db_url, body)


def test_an_approved_mention_closes_a_gap_in_the_gap_report(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await seed_audit(h)
        run_id = await seed_run(
            h,
            aid,
            mentioned=True,
            verified=False,
            competitors=["Rival"],
            urls=("https://g2.com/x",),
        )
        gaps = (await c.get(f"/audits/{aid}/gaps", headers=KEY)).json()["entries"]
        assert [e["domain"] for e in gaps] == [
            "g2.com"
        ]  # the unverified brand claim does not count
        await post(c, run_id, action="approve")
        assert (await c.get(f"/audits/{aid}/gaps", headers=KEY)).json()["entries"] == []

    scenario(migrated_db_url, body)


# --- review queue ---------------------------------------------------------------------------------------------


def test_the_queue_lists_flagged_unreviewed_runs_and_shrinks_as_they_are_reviewed(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await seed_audit(h)
        flagged = await seed_run(h, aid, q=0, verified=False)
        await seed_run(h, aid, q=1, verified=True)  # verified: nothing to review
        await seed_run(h, aid, q=2, status="failed")  # no mention: nothing to review
        items = (await c.get("/review/queue", headers=KEY)).json()["items"]
        assert [i["run_id"] for i in items] == [flagged]
        item = items[0]
        assert (
            item["audit_id"] == aid
            and item["brand"] == "Acme"
            and item["question"] == "Question 0?"
        )
        assert (
            item["verdict_reason"] == "because" and item["quote"] == "Acme is great for startups."
        )
        assert item["brand_mentioned"] is True and item["provider"] == "gemini"
        await post(c, flagged)
        assert (await c.get("/review/queue", headers=KEY)).json()["items"] == []

    scenario(migrated_db_url, body)


def test_queue_can_be_limited_to_one_audit_and_paginated(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        a, b = await seed_audit(h), await seed_audit(h)
        for q in range(5):
            await seed_run(h, a, q=q)
        await seed_run(h, b)
        only_a = (await c.get("/review/queue", params={"audit_id": a}, headers=KEY)).json()
        assert len(only_a["items"]) == 5 and only_a["next_cursor"] is None
        seen: list[str] = []
        cursor: str | None = None
        for _ in range(10):
            page = (
                await c.get(
                    "/review/queue",
                    params={"limit": 2, **({"cursor": cursor} if cursor else {})},
                    headers=KEY,
                )
            ).json()
            seen += [i["run_id"] for i in page["items"]]
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert len(seen) == 6 and len(set(seen)) == 6  # every item once, none missed
        assert (await c.get("/review/queue", params={"limit": 0}, headers=KEY)).status_code == 422
        assert (
            await c.get("/review/queue", params={"cursor": "garbage"}, headers=KEY)
        ).status_code == 422

    scenario(migrated_db_url, body)


# --- run lists and detail ---------------------------------------------------------------------------------------


def test_the_run_list_gives_one_row_per_run_for_the_dot_grid(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await seed_audit(h)
        await seed_run(h, aid, q=0, repeat=0, mentioned=True, verified=True)
        await seed_run(h, aid, q=0, repeat=1, mentioned=False, verified=True, position=None)
        await seed_run(h, aid, q=0, repeat=2, status="failed")
        rows = (await c.get(f"/audits/{aid}/runs", headers=KEY)).json()
        assert [(r["question_idx"], r["repeat_idx"]) for r in rows] == [(0, 0), (0, 1), (0, 2)]
        assert [r["brand_mentioned"] for r in rows] == [True, False, None]  # None: no usable answer
        assert [r["status"] for r in rows] == ["ok", "ok", "failed"]
        assert rows[0]["verdict_status"] == "verified" and rows[0]["reviewed"] is False
        assert "raw_answer" not in rows[0]  # the list stays light
        assert (await c.get(f"/audits/{uuid.uuid4()}/runs", headers=KEY)).status_code == 404

    scenario(migrated_db_url, body)


def test_run_detail_has_the_raw_answer_highlight_citations_and_their_evidence(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await seed_audit(h)
        run_id = await seed_run(
            h,
            aid,
            verified=True,
            urls=(
                "https://good.example/p",
                "https://down.example/p",
                "https://untouched.example/p",
            ),
        )
        async with h.session_factory() as s, s.begin():
            for url, status, on_page in (
                ("https://good.example/p", "ok", False),
                ("https://down.example/p", "unreachable", None),
            ):
                s.add(
                    EvidenceRow(
                        audit_id=uuid.UUID(aid),
                        url=url,
                        domain=url.split("//")[1].split("/")[0],
                        fetch_status=status,
                        brand_on_page=on_page,
                        source_type="other",
                        cited_domain="x",
                        fetch_reason="timeout",
                    )
                )
        d = (await c.get(f"/runs/{run_id}", headers=KEY)).json()
        assert d["raw_answer"] == RAW and d["audit_id"] == aid and d["status"] == "ok"
        h0 = d["highlights"][0]
        assert RAW[h0["start"] : h0["end"]] == "Acme is great for startups."
        assert d["verdict"]["status"] == "verified" and d["mention"]["brand_mentioned"] is True
        by_url = {x["url"]: x for x in d["citations"]}
        good, down, none = (
            by_url["https://good.example/p"],
            by_url["https://down.example/p"],
            by_url["https://untouched.example/p"],
        )
        assert good["source"] == "grounding_metadata" and good["evidence"]["fetch_status"] == "ok"
        assert (
            good["evidence"]["brand_on_page"] is False
        )  # the page was read and the brand is not on it
        assert (
            down["evidence"]["fetch_status"] == "unreachable"
            and down["evidence"]["brand_on_page"] is None
        )  # unread
        assert none["evidence"] is None  # never researched (a cap, or research off)

    scenario(migrated_db_url, body)


def test_run_detail_404s_and_reflects_the_latest_review(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        assert (await c.get(f"/runs/{uuid.uuid4()}", headers=KEY)).status_code == 404
        assert (await c.get("/runs/nope", headers=KEY)).status_code == 404
        aid = await seed_audit(h)
        run_id = await seed_run(h, aid)
        assert (await c.get(f"/runs/{run_id}", headers=KEY)).json()["review"] is None
        await post(c, run_id, action="reject", reason="wrong company")
        review = (await c.get(f"/runs/{run_id}", headers=KEY)).json()["review"]
        assert (
            review["action"] == "reject"
            and review["reason"] == "wrong company"
            and review["reviewer"]
        )

    scenario(migrated_db_url, body)


# --- locating a quote in the raw answer ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "quote", "expected"),
    [
        ("Acme is great. Rival too.", "Acme is great.", "Acme is great."),
        (
            "Honestly,   HubSpot\nis GREAT -- for CRM!",
            "hubspot is great for crm",
            "HubSpot\nis GREAT -- for CRM",
        ),
        (
            "People say “HubSpot isn’t cheap” often.",
            '"HubSpot isn\'t cheap"',
            "HubSpot isn’t cheap",
        ),
        ("A. Acme wins. B. Acme wins.", "Acme wins", "Acme wins"),  # first occurrence
        ("Acme, Inc. is large.", "acme inc is large", "Acme, Inc. is large"),
    ],
)
def test_locate_quote_finds_the_original_text(raw: str, quote: str, expected: str) -> None:
    span = locate_quote(raw, quote)
    assert span is not None and raw[span[0] : span[1]] == expected


@pytest.mark.parametrize(
    ("raw", "quote"),
    [
        ("Acme is great.", "Acme is terrible"),
        ("", "Acme"),
        ("Acme", ""),
        ("Acme", "..."),
        ("HubSpotty", "HubSpot"),
    ],
)
def test_locate_quote_returns_none_when_the_quote_is_not_there(raw: str, quote: str) -> None:
    assert locate_quote(raw, quote) is None


def test_locate_quote_only_matches_whole_words() -> None:
    assert locate_quote("Acme is greater", "Acme is great") is None

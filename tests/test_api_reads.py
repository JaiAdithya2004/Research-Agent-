"""GET /audits/{id}, /sources, /sources/{id}, /gaps and the OpenAPI document."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import psycopg
import pytest

from app.api.main import create_app
from app.db.models import Audit, PageSnapshot
from app.db.models import SourceEvidence as EvidenceRow
from tests.api_support import ApiHarness, build_harness
from tests.conftest import run_async
from tests.test_orchestrator import QUESTIONS, Answer

Conn = psycopg.Connection[Any]
KEY = {"X-API-Key": "test-key"}
BODY: dict[str, Any] = {
    "brand": "Acme",
    "category": "crm",
    "competitors": ["Rival"],
    "questions": QUESTIONS,
    "repeats": 2,
    "search_mode": "grounded",
}
T = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def scenario(
    url: str,
    body: Callable[[ApiHarness, httpx.AsyncClient], Awaitable[None]],
    **client_kw: Any,
) -> None:
    async def go() -> None:
        harness = build_harness(url, **client_kw)
        transport = httpx.ASGITransport(app=harness.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            try:
                await body(harness, client)
            finally:
                await harness.close()

    run_async(go)


async def create(c: httpx.AsyncClient, h: ApiHarness, idem: str = "k", **over: Any) -> str:
    r = await c.post("/audits", json={**BODY, **over}, headers={**KEY, "Idempotency-Key": idem})
    assert r.status_code == 202, r.text
    await h.queue.drain()
    return str(r.json()["id"])


# --- auth and 404 ------------------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", ["", "/sources", "/gaps", f"/sources/{uuid.uuid4()}"])
def test_every_read_endpoint_requires_the_api_key(
    migrated_db_url: str, db: Conn, suffix: str
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        r = await c.get(f"/audits/{uuid.uuid4()}{suffix}")
        assert r.status_code == 401

    scenario(migrated_db_url, body)


@pytest.mark.parametrize("suffix", ["", "/sources", "/gaps", f"/sources/{uuid.uuid4()}"])
@pytest.mark.parametrize("audit_id", [str(uuid.uuid4()), "not-a-uuid", "123"])
def test_unknown_or_malformed_audit_ids_are_404(
    migrated_db_url: str, db: Conn, suffix: str, audit_id: str
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        r = await c.get(f"/audits/{audit_id}{suffix}", headers=KEY)
        assert r.status_code == 404 and r.json()["detail"]

    scenario(migrated_db_url, body)


def test_health_needs_no_key(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        assert (await c.get("/health")).json() == {"status": "ok"}

    scenario(migrated_db_url, body)


# --- GET /audits/{id} ---------------------------------------------------------------------------------


def test_get_audit_returns_the_summary_and_never_the_idempotency_key(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await create(c, h, idem="super-secret-idempotency-key")
        r = await c.get(f"/audits/{aid}", headers=KEY)
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == aid and data["status"] == "complete" and data["brand"] == "Acme"
        assert data["request"]["questions"] == QUESTIONS
        s = data["summary"]
        assert s["total_runs"] == 4 and s["mention_rate"] == 0.5 and s["stability"] == 1.0
        assert s["pages_fetched"] == 2 and s["latency_p50_ms"] == 120
        assert [q["outcomes"] for q in s["question_stability"]] == [[True, True], [False, False]]
        assert "super-secret" not in r.text and "idempotency" not in r.text.lower()
        assert data["completed_at"] is not None

    scenario(migrated_db_url, body)


def test_a_running_audit_has_status_running_and_no_summary_yet(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        h.queue.hold()
        created = await c.post("/audits", json=BODY, headers={**KEY, "Idempotency-Key": "k"})
        got = (await c.get(f"/audits/{created.json()['id']}", headers=KEY)).json()
        assert got["status"] == "running" and got["summary"] is None and got["completed_at"] is None
        h.queue.release()
        await h.queue.drain()

    scenario(migrated_db_url, body)


# --- sources ---------------------------------------------------------------------------------------------


def test_sources_list_the_researched_pages(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await create(c, h)
        rows = (await c.get(f"/audits/{aid}/sources", headers=KEY)).json()
        assert sorted(r["url"] for r in rows) == [
            "https://news.example/pm",
            "https://reviews.example/crm",
        ]
        row = next(r for r in rows if r["domain"] == "reviews.example")
        assert (
            row["fetch_status"] == "ok" and row["brand_on_page"] is False and row["quote"] is None
        )
        uuid.UUID(row["id"])

    scenario(migrated_db_url, body)


def test_sources_filters_and_validation(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await create(c, h)
        base = f"/audits/{aid}/sources"
        assert len((await c.get(base, params={"source_type": "other"}, headers=KEY)).json()) == 2
        assert (await c.get(base, params={"source_type": "forum"}, headers=KEY)).json() == []
        assert len((await c.get(base, params={"fetch_status": "ok"}, headers=KEY)).json()) == 2
        assert (await c.get(base, params={"fetch_status": "blocked"}, headers=KEY)).json() == []
        only = (await c.get(base, params={"domain": "WWW.News.Example"}, headers=KEY)).json()
        assert [r["domain"] for r in only] == ["news.example"]
        assert (await c.get(base, params={"source_type": "blog"}, headers=KEY)).status_code == 422
        assert (await c.get(base, params={"fetch_status": "nope"}, headers=KEY)).status_code == 422

    scenario(migrated_db_url, body)


async def seed_audit_with_sources(h: ApiHarness) -> tuple[str, dict[str, str]]:
    """An audit row plus hand-made snapshots and evidence, to test the read paths exactly."""
    ids: dict[str, str] = {}
    async with h.session_factory() as s, s.begin():
        audit = Audit(
            idempotency_key=f"seed-{uuid.uuid4()}", request=BODY, brand="Acme", status="complete"
        )
        ok = PageSnapshot(
            url="https://good.example/p",
            fetched_at=T,
            status="ok",
            text="Acme is a fine CRM for teams.",
            content_hash="h1",
        )
        bad = PageSnapshot(
            url="https://bad.example/p",
            fetched_at=T,
            status="unreachable",
            text=None,
            content_hash=None,
        )
        s.add_all([audit, ok, bad])
        await s.flush()

        def ev(name: str, url: str, **kw: Any) -> EvidenceRow:
            data: dict[str, Any] = {
                "audit_id": audit.id,
                "url": url,
                "domain": url.split("//")[1].split("/")[0],
                "fetch_status": "ok",
                "brand_on_page": False,
                "source_type": "other",
                "cited_domain": url.split("//")[1].split("/")[0],
                "fetch_reason": "ok",
                "classification": "model",
            }
            data.update(kw)
            row = EvidenceRow(**data)
            s.add(row)
            ids[name] = str(row.id) if row.id else ""
            return row

        rows = {
            "good": ev(
                "good",
                "https://good.example/p",
                brand_on_page=True,
                quote="Acme is a fine CRM for teams.",
                snapshot_id=ok.id,
                source_type="review",
            ),
            "bad": ev(
                "bad",
                "https://bad.example/p",
                fetch_status="unreachable",
                brand_on_page=None,
                snapshot_id=bad.id,
                fetch_reason="timeout",
            ),
            "ssrf": ev(
                "ssrf",
                "https://sneaky.example/p",
                fetch_status="blocked",
                brand_on_page=None,
                fetch_reason="blocked_ip",
            ),
            "dns": ev(
                "dns",
                "https://nodns.example/p",
                fetch_status="unreachable",
                brand_on_page=None,
                fetch_reason="dns_failure",
            ),
        }
        await s.flush()
        ids.update({k: str(v.id) for k, v in rows.items()})
        ids["audit"] = str(audit.id)
        ids["snapshot_time"] = T.isoformat()
    return ids["audit"], ids


def test_source_detail_returns_the_snapshot_text_and_its_timestamp(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid, ids = await seed_audit_with_sources(h)
        r = await c.get(f"/audits/{aid}/sources/{ids['good']}", headers=KEY)
        data = r.json()
        assert r.status_code == 200
        assert data["snapshot_text"] == "Acme is a fine CRM for teams."
        assert data["quote"] in data["snapshot_text"]
        assert data["snapshot_fetched_at"].startswith("2026-11-03T12:00:00")
        assert data["content_hash"] == "h1" and data["brand_on_page"] is True

    scenario(migrated_db_url, body)


def test_an_unreadable_page_has_no_text_and_null_brand_on_page(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid, ids = await seed_audit_with_sources(h)
        data = (await c.get(f"/audits/{aid}/sources/{ids['bad']}", headers=KEY)).json()
        assert data["fetch_status"] == "unreachable" and data["brand_on_page"] is None
        assert data["snapshot_text"] is None and data["quote"] is None

    scenario(migrated_db_url, body)


def test_internal_network_reasons_are_collapsed_in_the_api(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid, ids = await seed_audit_with_sources(h)
        rows = {r["url"]: r for r in (await c.get(f"/audits/{aid}/sources", headers=KEY)).json()}
        assert rows["https://sneaky.example/p"]["fetch_reason"] == "blocked_by_policy"
        assert rows["https://nodns.example/p"]["fetch_reason"] == "blocked_by_policy"
        assert rows["https://bad.example/p"]["fetch_reason"] == "timeout"  # ordinary reasons stay
        assert rows["https://sneaky.example/p"]["fetch_status"] == "blocked"
        text = (await c.get(f"/audits/{aid}/sources", headers=KEY)).text
        assert "blocked_ip" not in text and "dns_failure" not in text

    scenario(migrated_db_url, body)


def test_a_source_cannot_be_read_through_another_audit(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        _, ids = await seed_audit_with_sources(h)
        other = await create(c, h, idem="other")
        r = await c.get(f"/audits/{other}/sources/{ids['good']}", headers=KEY)
        assert r.status_code == 404
        assert (
            await c.get(f"/audits/{ids['audit']}/sources/not-a-uuid", headers=KEY)
        ).status_code == 404

    scenario(migrated_db_url, body)


# --- gaps ---------------------------------------------------------------------------------------------------


def gap_answers(question: str, provider: str, search: bool, n: int) -> Answer:
    if question == QUESTIONS[0]:  # competitor only, via g2.com
        return Answer("Rival is the best CRM today.", ["https://g2.com/rival"])
    return Answer("Acme is great. Rival is fine too.", ["https://capterra.com/acme"])


def test_gap_report_lists_domains_cited_for_competitors_but_never_for_the_brand(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await create(c, h)
        data = (await c.get(f"/audits/{aid}/gaps", headers=KEY)).json()
        assert data["audit_id"] == aid
        assert [(e["domain"], e["frequency"], e["competitors"]) for e in data["entries"]] == [
            ("g2.com", 2, ["Rival"])
        ]
        sources = (
            await c.get(f"/audits/{aid}/sources", params={"domain": "g2.com"}, headers=KEY)
        ).json()
        assert data["entries"][0]["source_ids"] == [sources[0]["id"]]  # link to the evidence

    scenario(migrated_db_url, body, answers=gap_answers)


def test_gaps_for_an_audit_with_no_competitor_citations_is_empty(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await create(c, h)
        assert (await c.get(f"/audits/{aid}/gaps", headers=KEY)).json()["entries"] == []

    scenario(
        migrated_db_url,
        body,
        answers=lambda q, p, s, n: Answer("Acme is great.", ["https://x.example/a"]),
    )


# --- OpenAPI -----------------------------------------------------------------------------------------------------


def test_openapi_documents_the_contract_the_frontend_is_generated_from() -> None:
    spec = create_app(session_factory=object()).openapi()  # type: ignore[arg-type]
    paths = spec["paths"]
    for path in (
        "/audits",
        "/audits/{audit_id}",
        "/audits/{audit_id}/sources",
        "/audits/{audit_id}/sources/{source_id}",
        "/audits/{audit_id}/gaps",
        "/health",
    ):
        assert path in paths, path
    post = paths["/audits"]["post"]
    assert {"202", "422", "503"} <= set(post["responses"])
    names = {p["name"].lower() for p in post.get("parameters", [])}
    assert "idempotency-key" in names
    schemas = spec["components"]["schemas"]
    for name in (
        "AuditOut",
        "AuditRequest",
        "AuditSummary",
        "SourceOut",
        "GapReportOut",
        "QuestionStability",
    ):
        assert name in schemas, name

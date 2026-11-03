"""The committed OpenAPI file is the frontend's contract: it must match the running API."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_committed_openapi_schema_is_current() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/export_openapi.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_the_schema_documents_every_endpoint_the_frontend_needs() -> None:
    spec = json.loads((ROOT / "web" / "openapi.json").read_text(encoding="utf8"))
    paths = set(spec["paths"])
    needed = {
        "/audits",
        "/audits/{audit_id}",
        "/audits/{audit_id}/runs",
        "/audits/{audit_id}/sources",
        "/audits/{audit_id}/sources/{source_id}",
        "/audits/{audit_id}/gaps",
        "/audits/{audit_id}/events",
        "/runs/{run_id}",
        "/mentions/{run_id}/review",
        "/review/queue",
        "/evals/runs",
        "/health",
    }
    assert needed <= paths, needed - paths
    # The request constraints the new-audit form must mirror (SPEC: max 10 questions, repeats 1-5).
    request = spec["components"]["schemas"]["AuditRequest"]["properties"]
    assert request["questions"]["maxItems"] == 10 and request["questions"]["minItems"] == 1
    assert (request["repeats"]["minimum"], request["repeats"]["maximum"]) == (1, 5)

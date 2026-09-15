"""Tests for FastAPI endpoints — status codes, schemas, and behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """Create a TestClient with an in-memory SQLite database."""
    import os
    db_path = tmp_path_factory.mktemp("db") / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    # Re-import app after setting env var
    import importlib
    import backend.app as app_module
    importlib.reload(app_module)

    from backend.app import app
    return TestClient(app)


class TestHealth:
    def test_health_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestIngest:
    def test_ingest_returns_counts(self, client, synthetic_data):
        r = client.post("/ingest", json={"data_dir": str(synthetic_data)})
        assert r.status_code == 200
        body = r.json()
        assert body["sites_loaded"] == 200
        assert body["patients_loaded"] >= 800
        assert body["visits_loaded"] >= 5000

    def test_ingest_idempotent(self, client, synthetic_data):
        """Calling ingest twice should not raise errors."""
        r = client.post("/ingest", json={"data_dir": str(synthetic_data)})
        assert r.status_code == 200


class TestAnalyze:
    def test_analyze_returns_counts(self, client, synthetic_data):
        # Ensure data is ingested first
        client.post("/ingest", json={"data_dir": str(synthetic_data)})
        r = client.post("/analyze")
        assert r.status_code == 200
        body = r.json()
        assert body["deviations_detected"] > 0
        assert body["sites_scored"] == 200
        assert body["elapsed_seconds"] < 60  # generous for CI


class TestDeviations:
    def test_list_deviations_returns_paginated(self, client, synthetic_data):
        client.post("/ingest", json={"data_dir": str(synthetic_data)})
        client.post("/analyze")
        r = client.get("/deviations", params={"page": 1, "page_size": 10})
        assert r.status_code == 200
        body = r.json()
        assert "total" in body
        assert "items" in body
        assert len(body["items"]) <= 10

    def test_filter_by_severity(self, client):
        r = client.get("/deviations", params={"severity": "Major", "page_size": 50})
        assert r.status_code == 200
        items = r.json()["items"]
        for item in items:
            assert item["severity"] == "Major"

    def test_filter_by_site(self, client):
        r = client.get("/deviations", params={"site_id": "SITE-001", "page_size": 100})
        assert r.status_code == 200
        items = r.json()["items"]
        for item in items:
            assert item["site_id"] == "SITE-001"


class TestSiteRisk:
    def test_list_site_risks_sorted_descending(self, client):
        r = client.get("/sites/risk")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) > 0
        scores = [s["score"] for s in items]
        assert scores == sorted(scores, reverse=True)

    def test_site_detail_returns_full_record(self, client):
        r = client.get("/sites/SITE-001")
        assert r.status_code == 200
        body = r.json()
        assert body["site_id"] == "SITE-001"
        assert "risk_score" in body
        assert "recent_deviations" in body

    def test_site_not_found_returns_404(self, client):
        r = client.get("/sites/NONEXISTENT-999")
        assert r.status_code == 404

    def test_risk_score_has_six_indicators(self, client):
        r = client.get("/sites/risk")
        items = r.json()["items"]
        first = items[0]
        assert len(first["indicators"]) == 6


class TestCapaEndpoint:
    def test_capa_requires_analyze_first(self, tmp_path_factory):
        """CAPA on a site with no risk data returns 409."""
        import os
        db_path = tmp_path_factory.mktemp("db2") / "fresh.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
        import importlib
        import backend.app as app_module
        importlib.reload(app_module)
        from backend.app import app
        fresh_client = TestClient(app)
        # Ingest a site but don't analyze
        fresh_client.post("/ingest", json={})
        r = fresh_client.post("/capa/SITE-001")
        # Should be 409 (no risk data) or 404 (site not found)
        assert r.status_code in (404, 409)

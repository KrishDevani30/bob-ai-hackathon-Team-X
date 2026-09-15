"""End-to-end pipeline test — mock LLM, zero network calls."""

from __future__ import annotations

import pytest


def test_full_pipeline_mock_llm(synthetic_data, protocol_spec, mock_llm):
    """Full pipeline: detect → classify → score, all with mock LLM, no network."""
    import json

    visits = json.loads((synthetic_data / "visits.json").read_text())
    conmeds = json.loads((synthetic_data / "conmed_records.json").read_text())
    eligibility = json.loads((synthetic_data / "eligibility_data.json").read_text())
    sites = json.loads((synthetic_data / "sites.json").read_text())
    patients = json.loads((synthetic_data / "patients.json").read_text())

    # Step 1: Detection
    from backend.detection.engine import DetectionEngine
    engine = DetectionEngine(protocol_spec)
    deviations = engine.run(visits, conmeds, eligibility)
    assert len(deviations) > 0, "Pipeline must detect at least one deviation"

    # Step 2: Classification with mock LLM
    from backend.classification.severity import SeverityClassifier
    classifier = SeverityClassifier(mock_llm, protocol_spec)
    results = classifier.classify_batch(deviations[:100])  # classify first 100
    assert len(results) == 100
    for r in results:
        assert r.severity is not None
        assert r.source in ("llm", "rule_override", "fallback")

    # Attach severity to deviations
    for dev, res in zip(deviations[:100], results):
        dev.severity = res.severity.value
        dev.severity_source = res.source

    # Step 3: Risk scoring
    from backend.risk.scoring import score_sites
    scores = score_sites(deviations, sites, patients, visits)
    assert len(scores) == len(sites)
    assert all(0 <= s.score <= 100 for s in scores)
    assert scores[0].score >= scores[-1].score  # sorted descending

    # Step 4: CAPA generation
    from backend.capa.generator import generate_capa
    top_site = scores[0]
    site_info = next((s for s in sites if s["site_id"] == top_site.site_id), {})
    site_devs = [d for d in deviations if d.site_id == top_site.site_id]

    report = generate_capa(
        site_id=top_site.site_id,
        deviations=site_devs,
        risk_score=top_site,
        site_info=site_info,
        study_id=protocol_spec.study_id,
        protocol_version=protocol_spec.protocol_version,
        llm=mock_llm,
    )
    assert report.site_id == top_site.site_id
    assert report.capa_content is not None
    assert len(report.capa_content.regulatory_references) > 0
    assert "DRAFT" in report.generated_by or report.generated_by != ""

    print(f"\n[e2e] {len(deviations)} deviations, top site {top_site.site_id} score={top_site.score:.1f}")


def test_no_network_calls_made(synthetic_data, protocol_spec):
    """Mock LLM must not make any HTTP calls."""
    import socket

    original_connect = socket.socket.connect

    def fail_connect(self, *args):
        raise RuntimeError("Network call detected during mock LLM test!")

    socket.socket.connect = fail_connect
    try:
        from backend.llm.mock import MockLLMClient
        llm = MockLLMClient()
        result = llm.complete("system", "deviation_type: out_of_window")
        import json
        parsed = json.loads(result)
        assert "severity" in parsed
    finally:
        socket.socket.connect = original_connect

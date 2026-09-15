"""FastAPI application — Clinical Trial Risk Monitor API."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.capa.export import export_docx, export_pdf
from backend.capa.generator import generate_capa
from backend.classification.severity import SeverityClassifier
from backend.detection.engine import DetectionEngine, load_engine_from_files
from backend.llm.mock import MockLLMClient
from backend.models import (
    ConmedRecord,
    DeviationRecord,
    PatientRecord,
    SiteRecord,
    SiteRiskRecord,
    VisitRecord,
    create_tables,
    get_engine,
    get_session_factory,
)
from backend.protocol.loader import load_protocol
from backend.risk.scoring import SiteRiskScore, score_sites
from backend.schemas import (
    AnalyzeResponse,
    CapaGenerateResponse,
    DeviationListResponse,
    DeviationOut,
    IngestRequest,
    IngestResponse,
    SiteDetailOut,
    SiteRiskListResponse,
    SiteRiskOut,
)

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent / "data"
_REPORTS_DIR = Path(__file__).parent.parent / "reports"
_REPORTS_DIR.mkdir(exist_ok=True)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./ctrm.db")
engine = get_engine(DATABASE_URL)
SessionFactory = get_session_factory(engine)
create_tables(engine)

app = FastAPI(
    title="Clinical Trial Risk Monitor",
    description="Protocol deviation detection, severity classification, and site risk scoring.",
    version="1.0.0",
)


def get_db():
    db = SessionFactory()
    try:
        yield db
    finally:
        db.close()


def _get_llm():
    if os.environ.get("WATSONX_API_KEY"):
        from backend.llm.watsonx import WatsonxClient
        return WatsonxClient()
    return MockLLMClient()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ---------------------------------------------------------------------------
# POST /ingest
# ---------------------------------------------------------------------------

@app.post("/ingest", response_model=IngestResponse)
def ingest(request: IngestRequest, db: Session = Depends(get_db)):
    data_dir = Path(request.data_dir) if request.data_dir else _DATA_DIR

    def _read(name: str) -> list[dict]:
        p = data_dir / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

    sites = _read("sites.json")
    patients = _read("patients.json")
    visits = _read("visits.json")
    conmeds = _read("conmed_records.json")

    # Upsert sites
    for s in sites:
        rec = db.get(SiteRecord, s["site_id"]) or SiteRecord()
        for k, v in s.items():
            setattr(rec, k, v)
        db.merge(rec)

    # Upsert patients
    for p in patients:
        rec = PatientRecord(**{k: v for k, v in p.items() if hasattr(PatientRecord, k)})
        db.merge(rec)

    # Upsert visits
    for v in visits:
        rec = VisitRecord(
            visit_id=v["visit_id"],
            patient_id=v["patient_id"],
            site_id=v["site_id"],
            protocol_visit_id=v.get("protocol_visit_id"),
            scheduled_date=v.get("scheduled_date"),
            actual_date=v.get("actual_date"),
            dose_administered_mg=v.get("dose_administered_mg"),
            data_entry_date=v.get("data_entry_date"),
        )
        rec.set_assessments(v.get("assessments_completed") or [])
        db.merge(rec)

    # Upsert conmeds
    for c in conmeds:
        rec = ConmedRecord(**{k: v for k, v in c.items() if hasattr(ConmedRecord, k)})
        db.merge(rec)

    db.commit()
    return IngestResponse(
        sites_loaded=len(sites),
        patients_loaded=len(patients),
        visits_loaded=len(visits),
        conmeds_loaded=len(conmeds),
    )


# ---------------------------------------------------------------------------
# POST /analyze
# ---------------------------------------------------------------------------

@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(db: Session = Depends(get_db)):
    t0 = time.perf_counter()

    protocol_path = _DATA_DIR / "protocol_spec.json"
    spec = load_protocol(protocol_path)
    llm = _get_llm()

    visits = [_visit_to_dict(v) for v in db.query(VisitRecord).all()]
    conmeds = [_conmed_to_dict(c) for c in db.query(ConmedRecord).all()]
    patients = [_patient_to_dict(p) for p in db.query(PatientRecord).all()]
    sites = [_site_to_dict(s) for s in db.query(SiteRecord).all()]

    elig_data = [
        {
            "patient_id": p["patient_id"], "site_id": p["site_id"],
            "age": p.get("age"), "diagnosis_code": p.get("diagnosis_code"),
            "ecog_ps": p.get("ecog_ps"),
            "creatinine_clearance_ml_min": p.get("creatinine_clearance_ml_min"),
            "alt_x_uln": p.get("alt_x_uln"), "qtcf_ms": p.get("qtcf_ms"),
            "prior_cdk46_inhibitor": p.get("prior_cdk46_inhibitor"),
            "active_cns_mets": p.get("active_cns_mets"),
            "pregnant_or_breastfeeding": p.get("pregnant_or_breastfeeding"),
        }
        for p in patients
    ]

    detection_engine = DetectionEngine(spec)
    raw_deviations = detection_engine.run(visits, conmeds, elig_data)

    # Build prior history map for classifier
    prior_history: dict[str, list[str]] = {}
    for d in raw_deviations:
        prior_history.setdefault(d.patient_id, []).append(d.deviation_type)

    classifier = SeverityClassifier(llm, spec)
    results = classifier.classify_batch(raw_deviations, prior_history)

    # Persist deviations
    db.query(DeviationRecord).delete()
    for dev, result in zip(raw_deviations, results):
        dev.severity = result.severity.value
        dev.severity_source = result.source
        dev.rationale = result.rationale

        rec = DeviationRecord(
            deviation_id=dev.deviation_id,
            patient_id=dev.patient_id,
            site_id=dev.site_id,
            visit_id=dev.visit_id,
            rule_id=dev.rule_id,
            deviation_type=dev.deviation_type,
            detected_at=dev.detected_at,
            magnitude=dev.magnitude,
            is_safety_critical=dev.is_safety_critical,
            severity=dev.severity,
            severity_source=dev.severity_source,
            rationale=dev.rationale,
        )
        rec.set_context(dev.context)
        db.add(rec)

    # Score sites
    visit_count = len(visits)
    scores = score_sites(raw_deviations, sites, patients, visits)
    db.query(SiteRiskRecord).delete()
    for score in scores:
        rec = SiteRiskRecord(
            site_id=score.site_id,
            score=score.score,
            risk_band=score.risk_band,
            total_deviations=score.total_deviations,
            total_visits=score.total_visits,
            indicators_json=json.dumps([ind.model_dump() for ind in score.indicators]),
            computed_at=datetime.utcnow(),
        )
        db.add(rec)

    db.commit()
    elapsed = time.perf_counter() - t0
    return AnalyzeResponse(
        deviations_detected=len(raw_deviations),
        sites_scored=len(scores),
        elapsed_seconds=round(elapsed, 2),
    )


# ---------------------------------------------------------------------------
# GET /deviations
# ---------------------------------------------------------------------------

@app.get("/deviations", response_model=DeviationListResponse)
def list_deviations(
    site_id: str | None = Query(None),
    severity: str | None = Query(None),
    deviation_type: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = db.query(DeviationRecord)
    if site_id:
        q = q.filter(DeviationRecord.site_id == site_id)
    if severity:
        q = q.filter(DeviationRecord.severity == severity)
    if deviation_type:
        q = q.filter(DeviationRecord.deviation_type == deviation_type)
    if date_from:
        q = q.filter(DeviationRecord.detected_at >= date_from)
    if date_to:
        q = q.filter(DeviationRecord.detected_at <= date_to)

    total = q.count()
    items = q.offset((page - 1) * page_size).limit(page_size).all()
    return DeviationListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[_dev_record_to_out(r) for r in items],
    )


# ---------------------------------------------------------------------------
# GET /sites/risk
# ---------------------------------------------------------------------------

@app.get("/sites/risk", response_model=SiteRiskListResponse)
def list_site_risks(db: Session = Depends(get_db)):
    records = db.query(SiteRiskRecord).order_by(SiteRiskRecord.score.desc()).all()
    return SiteRiskListResponse(items=[_risk_record_to_out(r) for r in records])


# ---------------------------------------------------------------------------
# GET /sites/{site_id}
# ---------------------------------------------------------------------------

@app.get("/sites/{site_id}", response_model=SiteDetailOut)
def get_site_detail(site_id: str, db: Session = Depends(get_db)):
    site = db.get(SiteRecord, site_id)
    if not site:
        raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

    risk_rec = db.get(SiteRiskRecord, site_id)
    recent_devs = (
        db.query(DeviationRecord)
        .filter(DeviationRecord.site_id == site_id)
        .order_by(DeviationRecord.detected_at.desc())
        .limit(50)
        .all()
    )

    return SiteDetailOut(
        site_id=site.site_id,
        site_name=site.site_name,
        country=site.country,
        principal_investigator=site.principal_investigator,
        activation_date=site.activation_date,
        staff_count=site.staff_count,
        monitoring_visits_completed=site.monitoring_visits_completed,
        risk_score=_risk_record_to_out(risk_rec) if risk_rec else None,
        recent_deviations=[_dev_record_to_out(d) for d in recent_devs],
    )


# ---------------------------------------------------------------------------
# POST /capa/{site_id}
# ---------------------------------------------------------------------------

@app.post("/capa/{site_id}", response_model=CapaGenerateResponse)
def generate_capa_report(site_id: str, db: Session = Depends(get_db)):
    site = db.get(SiteRecord, site_id)
    if not site:
        raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

    risk_rec = db.get(SiteRiskRecord, site_id)
    if not risk_rec:
        raise HTTPException(status_code=409, detail="Run /analyze first to compute risk scores")

    devs = db.query(DeviationRecord).filter(DeviationRecord.site_id == site_id).all()
    spec = load_protocol(_DATA_DIR / "protocol_spec.json")
    llm = _get_llm()

    from backend.detection.models import Deviation as DevModel
    from backend.risk.scoring import SiteRiskScore, IndicatorBreakdown

    indicators = []
    if risk_rec.indicators_json:
        for ind in json.loads(risk_rec.indicators_json):
            indicators.append(IndicatorBreakdown(**ind))

    risk_score = SiteRiskScore(
        site_id=site_id,
        score=risk_rec.score,
        risk_band=risk_rec.risk_band,
        total_deviations=risk_rec.total_deviations,
        total_visits=risk_rec.total_visits,
        indicators=indicators,
    )

    deviation_models = [
        DevModel(
            deviation_id=d.deviation_id,
            patient_id=d.patient_id,
            site_id=d.site_id,
            visit_id=d.visit_id,
            rule_id=d.rule_id,
            deviation_type=d.deviation_type,
            detected_at=d.detected_at,
            magnitude=d.magnitude,
            context=d.get_context(),
            is_safety_critical=d.is_safety_critical,
            severity=d.severity,
            severity_source=d.severity_source,
            rationale=d.rationale,
        )
        for d in devs
    ]

    site_info = {
        "site_name": site.site_name,
        "country": site.country,
        "principal_investigator": site.principal_investigator,
    }

    report = generate_capa(
        site_id=site_id,
        deviations=deviation_models,
        risk_score=risk_score,
        site_info=site_info,
        study_id=spec.study_id,
        protocol_version=spec.protocol_version,
        llm=llm,
    )

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    docx_path = _REPORTS_DIR / f"CAPA_{site_id}_{timestamp}.docx"
    pdf_path = _REPORTS_DIR / f"CAPA_{site_id}_{timestamp}.pdf"
    export_docx(report, docx_path)
    export_pdf(report, pdf_path)

    return CapaGenerateResponse(
        site_id=site_id,
        report_date=report.report_date,
        risk_band=report.risk_band,
        download_docx=f"/reports/{docx_path.name}",
        download_pdf=f"/reports/{pdf_path.name}",
        narrative_preview=report.capa_content.model_dump(),
    )


@app.get("/reports/{filename}")
def download_report(filename: str):
    path = _REPORTS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if filename.endswith(".docx") else "application/pdf"
    )
    return FileResponse(str(path), media_type=media_type, filename=filename)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _visit_to_dict(v: VisitRecord) -> dict:
    return {
        "visit_id": v.visit_id, "patient_id": v.patient_id, "site_id": v.site_id,
        "protocol_visit_id": v.protocol_visit_id, "scheduled_date": v.scheduled_date,
        "actual_date": v.actual_date, "dose_administered_mg": v.dose_administered_mg,
        "assessments_completed": v.get_assessments(), "data_entry_date": v.data_entry_date,
    }


def _conmed_to_dict(c: ConmedRecord) -> dict:
    return {
        "record_id": c.record_id, "patient_id": c.patient_id, "site_id": c.site_id,
        "drug_name": c.drug_name, "atc_class": c.atc_class,
        "start_date": c.start_date, "stop_date": c.stop_date,
    }


def _patient_to_dict(p: PatientRecord) -> dict:
    return {
        "patient_id": p.patient_id, "site_id": p.site_id, "age": p.age,
        "sex": p.sex, "diagnosis_code": p.diagnosis_code, "enrollment_date": p.enrollment_date,
        "ecog_ps": p.ecog_ps, "creatinine_clearance_ml_min": p.creatinine_clearance_ml_min,
        "alt_x_uln": p.alt_x_uln, "qtcf_ms": p.qtcf_ms,
        "prior_cdk46_inhibitor": p.prior_cdk46_inhibitor,
        "active_cns_mets": p.active_cns_mets,
        "pregnant_or_breastfeeding": p.pregnant_or_breastfeeding,
    }


def _site_to_dict(s: SiteRecord) -> dict:
    return {
        "site_id": s.site_id, "site_name": s.site_name, "country": s.country,
        "principal_investigator": s.principal_investigator,
        "activation_date": s.activation_date, "staff_count": s.staff_count,
        "monitoring_visits_completed": s.monitoring_visits_completed,
    }


def _dev_record_to_out(r: DeviationRecord) -> DeviationOut:
    return DeviationOut(
        deviation_id=r.deviation_id, patient_id=r.patient_id, site_id=r.site_id,
        visit_id=r.visit_id, rule_id=r.rule_id, deviation_type=r.deviation_type,
        detected_at=r.detected_at, magnitude=r.magnitude, context=r.get_context(),
        is_safety_critical=r.is_safety_critical, severity=r.severity,
        severity_source=r.severity_source, rationale=r.rationale,
    )


def _risk_record_to_out(r: SiteRiskRecord) -> SiteRiskOut:
    from backend.schemas import IndicatorBreakdownOut
    indicators = []
    if r.indicators_json:
        for ind in json.loads(r.indicators_json):
            indicators.append(IndicatorBreakdownOut(**ind))
    return SiteRiskOut(
        site_id=r.site_id, score=r.score, risk_band=r.risk_band,
        total_deviations=r.total_deviations, total_visits=r.total_visits,
        indicators=indicators,
    )

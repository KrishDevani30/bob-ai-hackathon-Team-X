# Clinical Trial Risk Monitor (CTRM)

> **⚠️ PROTOTYPE NOTICE**: This is a decision-support proof-of-concept built on synthetic data. It is **not** a validated GxP system. All outputs — deviations, severity classifications, risk scores, and CAPA reports — require review by a qualified clinical research professional before any regulatory submission, site communication, or operational decision.

---

## Problem

A Phase III oncology trial spanning 200+ investigational sites generates 5,000+ patient visit records. Protocol deviations — missed visits, out-of-window visits, incorrect dosing, prohibited concomitant medications, missing safety assessments — currently go undetected until an FDA audit. A rejected submission delays approval 6–12 months and costs $50–100M.

CTRM gives risk managers real-time, site-level visibility into emerging problems **before** they become audit findings.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Streamlit Dashboard                       │
│   Overview · Site Heatmap · Drill-Down · CAPA Generator         │
└─────────────────────────────┬───────────────────────────────────┘
                              │ HTTP / REST
┌─────────────────────────────▼───────────────────────────────────┐
│                          FastAPI Backend                         │
│  POST /ingest  POST /analyze  GET /deviations  GET /sites/risk  │
│  GET /sites/{id}  POST /capa/{id}  GET /health                  │
└──┬──────────────┬─────────────────┬──────────────┬──────────────┘
   │              │                 │              │
   ▼              ▼                 ▼              ▼
Protocol     Detection         Severity        Risk
 Loader       Engine          Classifier      Scoring
(JSON →       (7 rules,       (LLM +          (6 leading
 Pydantic)    pandas,         rule overrides,  indicators,
              vectorized)     ICH E6(R2))      0–100 score)
                                    │
                              ┌─────▼──────┐
                              │ LLMClient  │
                              │  interface │
                              ├────────────┤
                              │ MockLLM    │ ← default (no key needed)
                              │ WatsonxLLM │ ← set WATSONX_API_KEY
                              └────────────┘
                                    │
                              ┌─────▼──────┐
                              │    CAPA    │
                              │ Generator  │
                              │ DOCX / PDF │
                              └────────────┘
                                    │
                              ┌─────▼──────┐
                              │  SQLite /  │
                              │ PostgreSQL │
                              │   Db2      │
                              └────────────┘
```

---

## Repository Structure

```
ctrm/
  src/
    backend/
      app.py                  FastAPI routes
      models.py               SQLAlchemy ORM models
      schemas.py              Pydantic request/response schemas
      protocol/
        loader.py             Parse + validate protocol spec
        schema.py             Protocol spec Pydantic models
      detection/
        models.py             Deviation data model
        rules.py              7 individual detection rules
        engine.py             Orchestrates rules over dataset
      classification/
        severity.py           Hybrid LLM + rule-override classifier
        prompts.py            ICH E6(R2) system prompts
      risk/
        config.py             Risk weights config (tunable)
        indicators.py         6 leading indicator computations
        scoring.py            0–100 risk score with breakdown
      capa/
        generator.py          LLM-grounded CAPA narrative
        export.py             DOCX + PDF rendering
      llm/
        client.py             Abstract LLMClient interface
        watsonx.py            IBM watsonx.ai / Granite implementation
        mock.py               Deterministic mock (no API key needed)
    data/
      protocol_spec.json      Machine-readable protocol for ONC-2024-001
      generate_synthetic_data.py  200 sites, 800 patients, 5200+ visits
    dashboard/
      app.py                  Streamlit 4-view dashboard
    tests/
      conftest.py             Shared fixtures
      test_protocol.py        Protocol schema + loader tests
      test_rules.py           Detection rule unit tests (boundary cases)
      test_recall.py          Recall ≥ 0.95 against ground_truth.json
      test_severity.py        Classifier + override tests
      test_risk.py            Risk scoring breakdown tests
      test_api.py             FastAPI endpoint tests
      test_e2e.py             Full pipeline mock-LLM test
  requirements.txt
  pytest.ini
```

---

## Setup

### Prerequisites

- Python 3.11+
- pip

### Install

```bash
cd ctrm
pip install -r requirements.txt
```

### Generate synthetic data

```bash
cd src
python data/generate_synthetic_data.py --seed 42
```

This writes `sites.json`, `patients.json`, `visits.json`, `conmed_records.json`, `eligibility_data.json`, and `ground_truth.json` into `src/data/`.

### Run the backend

```bash
cd src
uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000
```

### Run the dashboard

```bash
cd src
streamlit run dashboard/app.py
```

Open http://localhost:8501, then use the sidebar buttons to **Ingest Data** and **Run Analysis**.

### Run tests

```bash
cd ctrm
pytest
```

---

## Protocol Spec Schema

`data/protocol_spec.json` is validated against `backend/protocol/schema.py` (Pydantic v2) on load. Key fields:

| Field | Type | Description |
|---|---|---|
| `study_id` | string | Unique study identifier |
| `protocol_version` | string | Protocol version string |
| `visit_schedule` | array | Visit definitions (see below) |
| `dosing` | object | Drug, planned dose, acceptable range |
| `prohibited_medications` | array | Drug name, ATC class, reason, `severity_floor` |
| `required_assessments` | dict | Assessment code → name + `is_safety_critical` |
| `eligibility_criteria` | object | Inclusion / exclusion criteria with checkable bounds |

**Visit definition fields**: `visit_id`, `visit_name`, `target_day` (days from baseline), `window_before_days`, `window_after_days`, `is_safety_critical`, `required_assessments` (array of codes).

The loader fails loudly with a clear Pydantic validation error on any schema violation.

---

## Detection Rules

| Rule ID | Rule | Output |
|---|---|---|
| R001 | `check_visit_window` | `out_of_window` — magnitude in days |
| R002 | `check_missed_visit` | `missed_visit` — window must have closed |
| R003 | `check_dosing` | `dosing_deviation` — % deviation from planned |
| R004 | `check_prohibited_medication` | `prohibited_medication` — conmed/treatment overlap |
| R005 | `check_missing_assessments` | `missing_assessment` — count of missing |
| R006 | `check_late_data_entry` | `late_data_entry` — business-day lag > 5 |
| R007 | `check_eligibility_violation` | `eligibility_violation` — criterion violated |

All rules use vectorized pandas operations. The full 5,200-visit dataset runs in < 10 seconds.

---

## Severity Classification

Classification is hybrid: LLM first, then mandatory deterministic overrides.

### ICH E6(R2) Severity Definitions

| Severity | Definition |
|---|---|
| **Major** | Significant effect on patient safety, rights, or data integrity/reliability |
| **Minor** | Unlikely to significantly affect safety, rights, or data integrity |
| **Administrative** | Procedural/documentation only — no safety or data impact |

### LLM Flow

1. Build a structured user message: deviation type, magnitude, context dict, patient prior history
2. Call `LLMClient.complete(SEVERITY_SYSTEM_PROMPT, user_message)`
3. Parse response JSON: `{severity, rationale, ich_reference, confidence}`
4. Validate with Pydantic; retry once on parse failure; fall back to conservative rule-only severity if both attempts fail
5. Log classification with source (`llm` / `rule_override` / `fallback`)

### Deterministic Rule Overrides (non-negotiable, can only raise severity)

| Condition | Minimum Severity |
|---|---|
| `eligibility_violation` | **Major** |
| `prohibited_medication` | As per `severity_floor` in spec |
| `is_safety_critical = true` | **Minor** |

Overrides are applied **after** the LLM response. They can only **raise** severity, never lower it.

### Human-in-the-Loop Boundary

The LLM drafts severity rationales and CAPA narratives. A qualified clinical research professional must:
- Review all Major deviations before site communication
- Review and approve all CAPA reports before submission or implementation
- Validate risk scores before triggering regulatory escalation actions

---

## Risk Scoring Formula

Score = Σ(normalized_indicator × weight), clamped to [0, 100].

| Indicator | Description | Normalization Max | Default Weight |
|---|---|---|---|
| `severity_weighted_rate` | Weighted devs per 100 visits (Major=10, Minor=3, Admin=1) | 150 | **0.35** |
| `recurrence_rate` | % of deviation types that repeat at site | 100 | **0.20** |
| `mean_time_to_detection` | Mean data entry lag in calendar days | 20 days | **0.15** |
| `deviation_trend` | Recent 90-day rate vs prior 90-day rate | ±100 → 0–100 | **0.15** |
| `enrollment_velocity_risk` | Enrollment quartile × deviation rate | 100 | **0.10** |
| `monitoring_coverage` | Visits per completed monitoring visit | 50 | **0.05** |

**Sum of weights = 1.00** (validated at startup).

### Risk Bands

| Score | Band |
|---|---|
| 0 – 29 | 🟢 Low |
| 30 – 59 | 🟡 Medium |
| 60 – 79 | 🟠 High |
| 80 – 100 | 🔴 Critical |

Weights are exposed in `backend/risk/config.py` and can be tuned by risk managers without code changes.

---

## Configuring watsonx.ai

Set these environment variables to use IBM Granite instead of the mock:

```bash
export WATSONX_API_KEY=<your-api-key>
export WATSONX_PROJECT_ID=<your-project-id>
export WATSONX_URL=https://us-south.ml.cloud.ibm.com   # optional
export WATSONX_MODEL_ID=ibm/granite-13b-chat-v2         # optional
```

Install the SDK:

```bash
pip install ibm-watsonx-ai
```

---

## Database

SQLite is used by default. To switch to PostgreSQL or Db2:

```bash
export DATABASE_URL=postgresql://user:pass@host:5432/ctrm
# or
export DATABASE_URL=db2+ibm_db://user:pass@host:50000/ctrm
```

---

## Limitations

1. **Synthetic data only** — all patient, site, and visit records are procedurally generated. No real clinical data is used.
2. **Not a validated GxP system** — this prototype has not undergone IQ/OQ/PQ validation, 21 CFR Part 11 electronic record controls, or audit trail requirements for regulatory submission.
3. **LLM hallucination risk** — all LLM outputs are schema-validated and grounded-in-data prompts are used, but LLM-generated text must be reviewed by a qualified professional before use.
4. **Rule-based detection limitations** — detection relies on structured data fields. Deviations embedded in free-text notes or unstandardized source data will not be caught.
5. **Risk score is a leading indicator, not a ground truth** — weights are configurable heuristics, not statistically validated predictors of audit outcomes.
6. **No real-time data integration** — this prototype ingests flat JSON files; production deployment would require integration with a validated EDC system (e.g., Medidata Rave, Veeva Vault CDMS).

---

## License

Prototype — not for production or regulatory use.

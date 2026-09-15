# Solution Overview

## What We Built

Clinical Trial Risk Monitor is a decision-support application for identifying and prioritizing clinical-trial protocol risks. It loads a machine-readable protocol and trial records, detects deviations with reproducible rules, classifies their severity, calculates a transparent risk score for each site, and presents the results in a Streamlit dashboard. It can use IBM watsonx.ai for live language-model assistance, while a deterministic mock client supports local operation without an API key.

## How It Works

The system processes protocol and trial data through the following workflow:

1. The user generates or supplies the protocol, site, patient, visit, eligibility, and concomitant-medication data, then uses the dashboard to ingest it through the FastAPI backend.
2. The protocol loader validates the protocol specification, and the detection engine applies seven rules for visit windows, missed visits, dosing, prohibited medications, missing assessments, late data entry, and eligibility.
3. The severity classifier produces a rationale using the configured LLM client and applies deterministic overrides for eligibility, prohibited-medication, and safety-critical findings.
4. The risk scorer aggregates six leading indicators into a 0–100 site score, which the dashboard exposes through overview, site-risk, and deviation drill-down views.
5. Users can generate CAPA narratives for findings and export the resulting reports to DOCX or PDF.

## Architecture Diagram

> See [`architecture.md`](architecture.md) for the detailed diagram.

The main flow is:

```
[User] → [Streamlit Dashboard] → [FastAPI API] → [Protocol + Detection + Risk]
                                  ↓                    ↓
                           [SQLite/PostgreSQL]   [Mock LLM or watsonx.ai]
                                  ↓
                            [CAPA DOCX/PDF]
```

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Protocol rules are deterministic and protocol-aware | Reproducible findings make the system easier to audit, test, and explain to clinical operations teams. |
| LLM classification is paired with mandatory rule overrides | Language-model reasoning can provide useful rationales while safety and eligibility conditions retain conservative severity floors. |
| SQLite and a mock LLM are the local defaults | Judges can run the complete prototype without provisioning a database or API credentials, while PostgreSQL and watsonx.ai remain available for deployment. |

## IBM Technologies Used

IBM technologies are used at the model-assistance and development-workflow boundaries:

- **IBM watsonx.ai:** The optional `WatsonxClient` calls an IBM Granite model through the `ibm-watsonx-ai` SDK to draft severity classifications, rationales, and CAPA content. The project passes `WATSONX_API_KEY`, `WATSONX_PROJECT_ID`, and optional model or URL settings through environment variables.
- **IBM Bob:** IBM Bob was used as the AI development assistant to help implement, refine, and document the clinical-trial risk-monitoring prototype.

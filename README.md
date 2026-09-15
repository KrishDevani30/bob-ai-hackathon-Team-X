# Clinical Trial Risk Monitor

> ⚠️ **Prototype notice:** This decision-support proof of concept uses synthetic data. It is not a validated GxP system; qualified clinical research professionals must review all outputs before operational or regulatory use.

---

## 👥 Team

| Field | Value |
|---|---|
| **Team Name** | Team-X |
| **Track** | AI |
| **Team Lead** | Krish Devani — 23it023@charusat.edu.in |
| **Members** | Prince Diyora, Dhruv Gabani, Dishant Nakrani |

---

## 🎯 Problem Statement

Clinical trial risk managers and clinical operations teams need to identify protocol deviations before they become audit findings. In large oncology trials, missed or out-of-window visits, dosing issues, prohibited medications, missing safety assessments, late data entry, and eligibility violations can be difficult to detect across thousands of records, potentially delaying submissions and increasing compliance risk.

---

## 💡 Solution

Clinical Trial Risk Monitor is a FastAPI and Streamlit decision-support application that analyzes protocol and visit data, detects deviations, classifies severity, and scores site-level risk. It combines deterministic protocol rules with optional watsonx.ai-assisted classification, then generates CAPA narratives and exportable reports so teams can investigate emerging issues earlier.

---

## ✨ Key Features

- **Protocol-aware deviation detection:** Seven rules identify visit-window, missed-visit, dosing, prohibited-medication, missing-assessment, late-entry, and eligibility deviations.
- **Hybrid severity classification:** Optional LLM reasoning is combined with deterministic ICH E6(R2)-aligned overrides that can only raise severity.
- **Site-level risk scoring:** Six leading indicators produce a transparent 0–100 score with a breakdown for investigation.
- **Operational dashboard:** Streamlit views provide overview metrics, site risk heatmaps, deviation drill-down, and analysis controls.
- **CAPA generation and export:** LLM-grounded corrective and preventive action narratives can be rendered to DOCX and PDF.

---

## 🛠️ Tech Stack

| Category | Technologies |
|---|---|
| **Languages** | Python |
| **Frameworks** | FastAPI, Streamlit, Pydantic, SQLAlchemy |
| **IBM Technologies** | IBM watsonx.ai with Granite integration, IBM Bob |
| **Databases** | SQLite by default; PostgreSQL and Db2-compatible configuration supported |
| **Other** | pandas, NumPy, Plotly, pytest, python-docx, ReportLab, Uvicorn |

---

## 📁 Repository Structure

```
├── src/                  # All source code
├── docs/                 # Written documentation
│   ├── problem-statement.md
│   ├── solution-overview.md
│   ├── architecture.md
│   └── setup-guide.md
├── demo/                 # Demo artifacts
│   ├── screenshots/      # App screenshots
│   └── demo-video-link.txt  # Link to demo video
├── presentation/         # Slide deck
└── submission.yaml       # Structured submission metadata
```

---

## ⚡ How to Run

> These commands run the local prototype from the repository root.

```bash
# 1. Install backend dependencies
cd src
pip install -r requirements.txt

# 2. Generate the synthetic trial dataset
python data/generate_synthetic_data.py --seed 42

# 3. Start the FastAPI backend
uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000
```

In a second terminal, from `src/`, start the dashboard:

```bash
streamlit run dashboard/app.py
```

Open `http://localhost:8501`, use the sidebar to ingest data and run analysis, and access the API at `http://localhost:8000/docs`.

Run the test suite from `src/` with:

```bash
pytest
```

---

## 🖥️ Demo

| Artifact | Link |
|---|---|
| 📹 Demo Video | [See demo/demo-video-link.txt](demo/demo-video-link.txt) — link pending |
| 🌐 Live Demo | [See demo/live-demo-url.txt](demo/live-demo-url.txt) — not deployed; run locally |
| 🖼️ Screenshots | [See demo/screenshots/](demo/screenshots/) |
| 📊 Presentation | [See presentation/](presentation/) |

---

## ⚠️ Known Limitations

> Be honest — judges appreciate transparency over overclaiming.

- The prototype uses synthetic clinical-trial data and is not validated for GxP or regulatory use.
- Authentication, authorization, and production deployment controls are not implemented.
- watsonx.ai integration is optional; the default deterministic mock enables local demos without API credentials.
- The demo video and live deployment links have not been supplied yet.

---

## 🏅 What We're Most Proud Of

Our strongest work is the transparent, protocol-aware risk pipeline: deterministic detection rules provide reproducible findings, while the hybrid classifier preserves human oversight by enforcing severity floors for safety-critical and eligibility-related deviations. The dashboard makes those findings actionable at both site and deviation level.

---

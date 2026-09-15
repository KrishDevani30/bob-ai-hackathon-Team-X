# Setup Guide

> **This file is read by the automated evaluation pipeline. Be precise and complete.**

## Prerequisites

Before you begin, ensure you have the following installed:

- [ ] Python 3.11 or newer
- [ ] pip
- [ ] An IBM Cloud account with watsonx.ai access (optional; only needed for live LLM calls)

## Environment Variables

The application runs locally with SQLite and a deterministic mock LLM by default, so no environment file is required. To enable live watsonx.ai calls, set these variables in the shell before starting the backend:

```bash
cp .env.example .env
```

| Variable | Description | Required |
|---|---|---|
| `WATSONX_API_KEY` | Your IBM watsonx.ai API key; enables the live client | Optional |
| `WATSONX_PROJECT_ID` | Your watsonx.ai project ID | Required with `WATSONX_API_KEY` |
| `WATSONX_URL` | watsonx.ai service URL; defaults to `https://us-south.ml.cloud.ibm.com` | No |
| `WATSONX_MODEL_ID` | Granite model ID; defaults to `ibm/granite-13b-chat-v2` | No |
| `DATABASE_URL` | SQLAlchemy database URL; defaults to `sqlite:///./ctrm.db` | No |

## Installation

```bash
# 1. From the repository root, enter the source directory
cd src

# 2. Install backend dependencies
pip install -r requirements.txt

# 3. Generate the deterministic synthetic trial dataset
python data/generate_synthetic_data.py --seed 42

# 4. Database tables are created automatically when the backend starts
```

## Running the Application

```bash
# Start the backend from src/
uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000

# Start the dashboard in a second terminal from src/
streamlit run dashboard/app.py
```

The dashboard will be available at `http://localhost:8501` and the API documentation at `http://localhost:8000/docs`.

## Running Tests

```bash
pytest
```

## Quick Demo (Optional)

If you have a demo script or sample data to showcase the project quickly:

```bash
python data/generate_synthetic_data.py --seed 42
# Then open http://localhost:8501, click Ingest Data, and click Run Analysis.
```

## Troubleshooting

| Issue | Solution |
|---|---|
| `ModuleNotFoundError` | Confirm that the active Python environment is the one where `pip install -r requirements.txt` was run. |
| The dashboard cannot reach the API | Start the backend first with `uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000`, then start Streamlit. |
| watsonx.ai authentication error | Verify `WATSONX_API_KEY` and `WATSONX_PROJECT_ID`, or unset `WATSONX_API_KEY` to use the local mock client. |

# Streamlit Frontend

## Purpose

This UI wraps the existing FastAPI API for demo use.

## Start Backend

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Start Frontend

```bash
streamlit run frontend/streamlit_app.py
```

## Environment

```text
STREAMLIT_API_BASE_URL=http://127.0.0.1:8000
```

## Demo Flow

1. Health check
2. Select template
3. Create task
4. Inspect plan
5. Run task
6. Inspect trace and adaptive/deepening phase
7. Read the final report and its five-dimensional quality score
8. Inspect local quality trends, routing state, and Few-shot cold start
9. HITL confirm if needed

The Streamlit fallback does not treat an intermediate Planned or initial ReAct
report as final. It keeps polling until the adaptive quality gate or deep
research cycle reaches the stable terminal state.

## Security

- Reads `.env` from the project root when `python-dotenv` is installed (falls back silently).
- Does not display API keys.
- Calls FastAPI only.

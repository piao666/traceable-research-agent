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
6. Inspect trace
7. Read report
8. HITL confirm if needed

## Security

- Does not read `.env`.
- Does not display API keys.
- Calls FastAPI only.

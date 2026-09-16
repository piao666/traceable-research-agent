# syntax=docker/dockerfile:1
# Pin the Debian release so Playwright's apt dependencies resolve against a
# stable package index instead of the floating release in python:3.11-slim.
FROM python:3.11-slim-bookworm AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_TIMEOUT=120
ENV PIP_RETRIES=5

WORKDIR /app

# Bootstrap before using --resume-retries (the base image may ship pip 24).
# Cache downloads outside image layers; preserve TLS and artifact hash checks.
RUN --mount=type=cache,id=traceable-pip-py311,target=/root/.cache/pip,sharing=locked \
    python -m pip install --upgrade pip==26.2.1

ENV PIP_RESUME_RETRIES=10

EXPOSE 8000 8501

CMD ["python", "scripts/docker_entrypoint.py"]

FROM runtime-base AS api-deps

COPY requirements/api.txt requirements/api.txt
RUN --mount=type=cache,id=traceable-pip-py311,target=/root/.cache/pip,sharing=locked \
    python -m pip install --prefer-binary -r requirements/api.txt \
    && python -m pip check \
    && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends chromium \
    && rm -rf /var/lib/apt/lists/*

FROM api-deps AS api

COPY . .
ENV PLAYWRIGHT_EXECUTABLE_PATH=/usr/bin/chromium

FROM api-deps AS streamlit-deps

COPY requirements/streamlit.txt requirements/streamlit.txt
RUN --mount=type=cache,id=traceable-pip-py311,target=/root/.cache/pip,sharing=locked \
    python -m pip install --prefer-binary -r requirements/api.txt -r requirements/streamlit.txt \
    && python -m pip check

FROM streamlit-deps AS streamlit

COPY . .
ENV PLAYWRIGHT_EXECUTABLE_PATH=/usr/bin/chromium

CMD ["streamlit", "run", "frontend/streamlit_app.py", "--server.port", "8501", "--server.address", "0.0.0.0", "--server.headless", "true"]

# Preserve docker build --target light and the default combined runtime image.
FROM streamlit AS light

CMD ["python", "scripts/docker_entrypoint.py"]

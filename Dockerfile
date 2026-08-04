FROM python:3.11-slim AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --prefer-binary --retries 20 --timeout 300 --default-timeout 300 -r requirements.txt

EXPOSE 8000 8501

CMD ["python", "scripts/docker_entrypoint.py"]

FROM runtime-base AS light

COPY . .

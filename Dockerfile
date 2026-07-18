FROM python:3.11-slim AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements-docker-light.txt .
RUN python -m pip install --no-cache-dir --prefer-binary --retries 20 --timeout 300 --default-timeout 300 -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn -r requirements-docker-light.txt

EXPOSE 8000 8501

CMD ["python", "scripts/docker_entrypoint.py"]

FROM runtime-base AS semantic-rag-deps

COPY requirements-docker-rag.txt .
RUN python -m pip install --no-cache-dir --prefer-binary --retries 20 --timeout 300 --default-timeout 300 --index-url https://download.pytorch.org/whl/cpu torch==2.13.0+cpu
RUN python -m pip install --no-cache-dir --prefer-binary --retries 20 --timeout 300 --default-timeout 300 -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn -r requirements-docker-rag.txt

FROM semantic-rag-deps AS semantic-rag

COPY . .

FROM runtime-base AS light

COPY . .

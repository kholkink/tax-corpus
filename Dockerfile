# tax-corpus: API + агент + задачи. Сборка: docker compose build
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/data/hf
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY sql ./sql
COPY templates ./templates
COPY scripts ./scripts
COPY data/parameters ./data/parameters
COPY data/calendar ./data/calendar
COPY data/interpretations ./data/interpretations
RUN pip install -e ".[agent,api,workspace,semantic]"
EXPOSE 8000
CMD ["uvicorn", "taxcorpus.api:app", "--host", "0.0.0.0", "--port", "8000"]

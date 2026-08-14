FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app
COPY pyproject.toml README.md ARCHITECTURE.md SECURITY.md PROVIDER.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir .

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn efds_agent.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]

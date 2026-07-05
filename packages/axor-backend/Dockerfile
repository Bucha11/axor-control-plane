FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY packages/axor-backend ./packages/axor-backend
RUN pip install --no-cache-dir uv && uv pip install --system ./packages/axor-backend
EXPOSE 8400
CMD ["uvicorn", "axor_backend.main:app", "--host", "0.0.0.0", "--port", "8400", "--factory"]

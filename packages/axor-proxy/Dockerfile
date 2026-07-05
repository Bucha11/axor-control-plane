FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY packages/axor-proxy ./packages/axor-proxy
RUN pip install --no-cache-dir uv && uv pip install --system ./packages/axor-proxy
EXPOSE 8401
ENTRYPOINT ["axor-proxy", "--host", "0.0.0.0", "--trace-dir", "/data/traces"]

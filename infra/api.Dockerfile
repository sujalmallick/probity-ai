FROM python:3.12-slim
RUN useradd -m probity
WORKDIR /app
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/api/src apps/api/src
RUN pip install --no-cache-dir -e "apps/api[live]"
COPY benchmark benchmark
USER probity
WORKDIR /app/apps/api
EXPOSE 8000
CMD ["sh", "-c", "python -m probity.demo.seed && uvicorn probity.api.main:app --host 0.0.0.0 --port 8000"]

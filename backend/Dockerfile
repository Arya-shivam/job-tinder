FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY jobmatch ./jobmatch
USER 65532:65532
EXPOSE 8000
CMD ["python", "-m", "jobmatch.main"]

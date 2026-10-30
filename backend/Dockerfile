FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

RUN useradd --create-home --uid 10001 burnrate
USER burnrate

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"
CMD ["uvicorn", "burnrate.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

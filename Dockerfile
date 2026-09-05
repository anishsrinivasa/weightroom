# One image, two commands: the API and the certification worker.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so code edits do not invalidate the layer.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY suites ./suites
COPY schemas ./schemas

# The eval sandbox runs on Modal, not here, so this image stays small: it needs
# the Modal client, not torch.
RUN useradd --create-home --uid 10001 keystone && chown -R keystone:keystone /app
USER keystone

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "keystone.settings:app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

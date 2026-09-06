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

# The package installs into site-packages while the suites tree stays
# beside the working directory, so nothing above the package resolves to
# it. Discovery falls back to the working directory, but naming it here
# means the image does not depend on where a process happens to start.
ENV KEYSTONE_SUITES_ROOT=/app/suites
COPY benchmarks ./benchmarks

# The gate's items live in `suites/*/assets`, which is gitignored -- a set in
# the repository is a set a rejected creator can practise against. A CI build
# therefore ships suites with no items, and every conditioning pair errors.
# The published release under `benchmarks/` is the same material, deliberately
# exported, so install it as the image's staged set. Rotate with
# `keystone stage` before this becomes the gating set for real traffic.
RUN keystone install-benchmarks --from benchmarks --into suites

# The eval sandbox runs on Modal, not here, so this image stays small: it needs
# the Modal client, not torch.
RUN useradd --create-home --uid 10001 keystone && chown -R keystone:keystone /app
USER keystone

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "keystone.settings:app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

web: uvicorn keystone.settings:app --factory --host 0.0.0.0 --port ${PORT:-8000}
worker: python -m keystone.cli worker --interval 30

# Backend API as a container (Railway, Render, Fly.io, Hugging Face Spaces, Cloud Run).
# Build from the backend folder. The slim models committed to git are copied in; data is not.
# MODELS_URL (app/ml/fetch_models.py) is only needed if backend/models is missing.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    INDICATORS_DIR=/app/data/raw/indicators

WORKDIR /app
COPY requirements.txt requirements-deploy.txt ./
RUN pip install --no-cache-dir -r requirements-deploy.txt

COPY app ./app
COPY models ./models
# x402 snapshot (about 6 MB, see data/x402/README.md); nothing else from data/ is copied.
COPY data/x402 ./data/x402

# Most hosts set PORT; 8000 when they do not.
EXPOSE 8000
CMD ["sh", "-c", "python -m app.ml.fetch_models && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]

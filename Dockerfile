FROM python:3.11-slim

# DejaVu fonts: Latin + Cyrillic for the learner PDFs
RUN apt-get update && \
    apt-get install -y --no-install-recommends fonts-dejavu-core curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD curl -sf http://localhost:8100/health || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8100"]

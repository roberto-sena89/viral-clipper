# Dockerfile for viral-clipper
# Build:  docker build -t viral-clipper .
# Run:    docker run --rm -v $(pwd)/output:/app/output viral-clipper <url> -o /app/output [flags]

FROM python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# App deps (cached layer)
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App source
COPY viralclipper/ ./viralclipper/
COPY config.example.yaml ./

# Ensure the package is importable regardless of WORKDIR
ENV PYTHONPATH=/app

# Runtime
USER 1000:1000
WORKDIR /app
VOLUME ["/app/output"]

ENTRYPOINT ["python", "-m", "viralclipper"]
CMD ["--help"]

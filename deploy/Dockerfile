# OSA - Open Science Assistant
# Compatible with HEDit deployment patterns
# Port allocation: HEDit prod=38427, HEDit dev=38428, OSA prod=38528, OSA dev=38529

FROM python:3.12-slim-bullseye

WORKDIR /app

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

# Install system dependencies (curl for healthcheck, consistent with HEDit)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Build argument for commit hash (set during CI build)
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=${GIT_COMMIT}

# Copy version.py first (required by hatchling for version detection)
# Then copy pyproject.toml and README.md for dependency installation
COPY src/version.py ./src/version.py
COPY pyproject.toml README.md ./

# Install uv and dependencies
RUN pip install uv && \
    uv pip install --system --no-cache ".[dev]"

# Copy the rest of the application code
COPY src/ ./src/

# Expose the application port (OSA prod = 38528)
EXPOSE 38528

# Health check (consistent with HEDit pattern)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:38528/health || exit 1

# Run the application
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "38528"]

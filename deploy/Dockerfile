# OSA - Open Science Assistant
# Compatible with HEDit deployment patterns
# Port allocation: HEDit prod=38427, HEDit dev=38428, OSA prod=38528, OSA dev=38529

FROM python:3.12-slim-bullseye

WORKDIR /app

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    DATA_DIR=/app/data

# Install system dependencies
# - curl: for healthcheck
# - gh: GitHub CLI for syncing issues/PRs
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | gpg --dearmor -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Build argument for commit hash (set during CI build)
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT_SHA=${GIT_COMMIT}

# Copy version.py first (required by hatchling for version detection)
# Then copy pyproject.toml and README.md for dependency installation
COPY src/version.py ./src/version.py
COPY pyproject.toml README.md ./

# Install uv and dependencies
RUN pip install uv && \
    uv pip install --system --no-cache ".[dev]"

# Copy the rest of the application code
COPY src/ ./src/

# Create data directory for knowledge database
# This should be mounted as a volume for persistence
RUN mkdir -p /app/data/knowledge

# Expose the application port (OSA prod = 38528)
EXPOSE 38528

# Health check (consistent with HEDit pattern)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:38528/health || exit 1

# Run the application
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "38528"]

# =============================================================================
# VOLUME MOUNT INSTRUCTIONS
# =============================================================================
#
# Mount the data directory as a volume to persist the knowledge database:
#
#   docker run -v /path/on/host/osa-data:/app/data ...
#
# Or in docker-compose:
#
#   volumes:
#     - ./data:/app/data
#
# =============================================================================
# ENVIRONMENT VARIABLES
# =============================================================================
#
# Required for full functionality:
#   GITHUB_TOKEN            - GitHub token for syncing issues/PRs (higher rate limits)
#
# Optional (for higher rate limits on paper sources):
#   SEMANTIC_SCHOLAR_API_KEY - Semantic Scholar API key
#   PUBMED_API_KEY          - PubMed/NCBI API key
#
# Sync scheduling (cron expressions):
#   SYNC_ENABLED            - Enable/disable auto sync (default: true)
#   SYNC_GITHUB_CRON        - GitHub sync schedule (default: "0 2 * * *" = daily 2am)
#   SYNC_PAPERS_CRON        - Papers sync schedule (default: "0 3 * * 0" = weekly Sun 3am)
#
# Data directory:
#   DATA_DIR                - Data directory path (default: /app/data)
#

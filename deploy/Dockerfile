# OSA - Open Science Assistant
# Compatible with HEDit deployment patterns
# Port allocation: HEDit prod=38427, HEDit dev=38428, OSA prod=38528, OSA dev=38529

FROM python:3.12-slim-bookworm

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
# Then copy pyproject.toml, the lockfile, and README.md for dependency installation
COPY src/version.py ./src/version.py
COPY pyproject.toml uv.lock README.md ./

# Install uv and dependencies from the lockfile, so the image ships exactly
# what was resolved and tested, not whatever PyPI resolves on build day.
# UV_PROJECT_ENVIRONMENT=/usr/local keeps packages where CMD below already
# finds them; --no-install-project is correct because the container runs
# from /app/src on sys.path (see CMD), never from the installed distribution.
RUN pip install uv && \
    UV_PROJECT_ENVIRONMENT=/usr/local uv sync --frozen --no-dev --extra server --no-install-project

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
# Required to answer requests (Claude Platform on AWS, not Amazon Bedrock):
#   ANTHROPIC_API_KEY       - Platform key for server-funded requests
#   ANTHROPIC_BASE_URL      - AWS-hosted Messages API endpoint
#   ANTHROPIC_WORKSPACE_ID  - Workspace the key is authorized on (wrkspc_...)
#                             See ../.env.example for the optional tuning vars.
#
# Required for full functionality:
#   GITHUB_TOKEN            - GitHub token for syncing issues/PRs (higher rate limits)
#
# Optional (for higher rate limits on paper sources):
#   SEMANTIC_SCHOLAR_API_KEY - Semantic Scholar API key
#   PUBMED_API_KEY          - PubMed/NCBI API key
#
# Knowledge sync:
#   SYNC_ENABLED            - Master switch for auto sync (default: true).
#                             Per-community schedules live in each community's
#                             config.yaml; there are no cron env vars.
#
# Data directory:
#   DATA_DIR                - Data directory path (default: /app/data)
#

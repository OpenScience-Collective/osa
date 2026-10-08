# OSA - Open Science Assistant
# Compatible with HEDit deployment patterns
# Port allocation: HEDit prod=38427, HEDit dev=38428, OSA prod=38528, OSA dev=38529

# hed-suggest, the tag lookup behind the HED assistant's suggest_hed_tags tool
# (src/assistants/hed/tools.py). It is the command-line program of hed-lsp's server
# package, which is not published to npm, so it is built here at a pinned commit.
# The lookup is a keyword search over the HED schema that ships inside hed-validator:
# no network at run time and no model download. onnxruntime and sharp serve only the
# optional --semantic mode, which the tool does not use, and are most of the install
# (about 340 MB of 450 MB), so they are removed.
#
# --semantic cannot work in this image, so do not add it to the tool: with it the
# program prints status lines to stdout ahead of the JSON, which breaks the tool's
# parsing; onnxruntime is gone, so the model cannot load; and the 613 MB model
# would download into /root/.cache, which is not a volume, on every new container.
#
# The Node tag floats on purpose: hed-validator needs Node 22 or newer, and a
# rebuild should pick up Node patch releases (the Python base image floats too).
# The hed-lsp commit and the lockfile are what pin the program itself.
#
# The last line of the RUN below is the build gate: it runs the program once and fails
# the build unless "button press" returns `Press`, a tag in HED 8.4.0. Do not assert
# `Button` there: it is the first keyword hit for "button", but it is not a tag in
# 8.4.0. hed-lsp's keyword map is not filtered against the schema, so some suggestions
# are not valid tags, and the HED assistant must still run validate_hed_string.
FROM node:22-bookworm-slim AS hed-lsp
ARG HED_LSP_REF=36447afb5e48bfecc45af0464b652614ba571414
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && npm install -g pnpm@10.33.4 \
    && git clone https://github.com/hed-standard/hed-lsp /opt/hed-lsp \
    && cd /opt/hed-lsp && git checkout "$HED_LSP_REF" \
    && pnpm install --frozen-lockfile --ignore-scripts --filter "hed-lsp-server..." \
    && pnpm --filter hed-lsp-server exec tsc -b \
    && rm -rf node_modules server/node_modules \
    && pnpm install --frozen-lockfile --prod --ignore-scripts --filter "hed-lsp-server..." \
    && rm -rf node_modules/.pnpm/onnxruntime-* node_modules/.pnpm/sharp@* node_modules/.pnpm/@img+sharp* \
        .git client docs images server/src \
    && node server/out/cli.js --json --top 10 "button press" | grep -q '"Press"'

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

# hed-suggest and the Node that runs it (see the hed-lsp stage above). The tool finds
# the program through HED_LSP_PATH.
COPY --from=hed-lsp /usr/local/bin/node /usr/local/bin/node
COPY --from=hed-lsp /opt/hed-lsp /opt/hed-lsp
ENV HED_LSP_PATH=/opt/hed-lsp

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
# To offer GPT-6 Luna, Qwen3 Next and gpt-oss-120b (Amazon Bedrock). Optional only if
# Claude Haiku will do for EEGLAB, BIDS and NWB, which default to GPT-6 Luna:
#   AWS_BEARER_TOKEN_BEDROCK - Bedrock API key that pays for those models. They are
#                             served only when ANTHROPIC_API_KEY (above) is set too.
#                             Without the Bedrock key they are left out of the model
#                             menu, a request naming one is answered with a 400, and
#                             a request to those three communities that names no model
#                             runs the deployment's Claude default (DEFAULT_MODEL, the Haiku
#                             class unless changed), logging an error naming the community.
#                             With no ANTHROPIC_API_KEY it goes to OpenRouter's Luna slug,
#                             or fails with HTTP 500.
#   BEDROCK_REGION          - Region the models are called in (default: us-east-2;
#                             Qwen3 Next is pinned to us-east-1, see anthropic_models.py)
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

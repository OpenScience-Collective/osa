#!/bin/bash

# auto-update.sh - Automatic Docker image update script for OSA
# This script checks for new Docker images and automatically updates the running container
#
# Usage:
#   ./auto-update.sh [options]
#
# Options:
#   --check-only            Only check for updates, don't deploy
#   --force                 Force update even if no new image available
#   --env ENV               Environment (prod|dev), default: prod
#   --rollback [N]          Redeploy the image from N deployments ago (default: 1,
#                           i.e. the one before the currently running image). Pins
#                           to that image's digest (not `:latest`) and writes a
#                           rollback lock, so hourly auto-updates won't immediately
#                           re-pull the bad `:latest` and undo the rollback.
#   --clear-rollback-lock   Resume normal auto-updates after a rollback, once the
#                           registry's `:latest` has actually been fixed.
#
# Setup as cron job (check every hour):
#   0 * * * * /path/to/deploy/auto-update.sh >> /var/log/osa/auto-update.log 2>&1

##### Configuration
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="${LOG_FILE:-/var/log/osa/auto-update.log}"
LOCK_FILE="/tmp/osa-update.lock"

# Default values
CHECK_ONLY=false
FORCE_UPDATE=false
ENVIRONMENT="prod"
DO_ROLLBACK=false
ROLLBACK_STEPS=1
CLEAR_ROLLBACK_LOCK=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --check-only)
            CHECK_ONLY=true
            shift
            ;;
        --force)
            FORCE_UPDATE=true
            shift
            ;;
        --env)
            ENVIRONMENT="$2"
            shift 2
            ;;
        --rollback)
            DO_ROLLBACK=true
            shift
            # Optional numeric argument: how many deployments back to go.
            if [[ $# -gt 0 && "$1" =~ ^[0-9]+$ ]]; then
                ROLLBACK_STEPS="$1"
                shift
            fi
            ;;
        --clear-rollback-lock)
            CLEAR_ROLLBACK_LOCK=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Set environment-specific variables
# Port allocation: HEDit prod=38427, HEDit dev=38428, OSA prod=38528, OSA dev=38529
if [ "$ENVIRONMENT" = "dev" ]; then
    IMAGE_NAME="osa-dev:latest"
    CONTAINER_NAME="osa-dev"
    REGISTRY_IMAGE="ghcr.io/openscience-collective/osa:dev"
    HOST_PORT=38529
    # Dev uses DEV_ROOT_PATH, defaults to /osa-dev
    ROOT_PATH_OVERRIDE="${DEV_ROOT_PATH:-/osa-dev}"
else
    IMAGE_NAME="osa:latest"
    CONTAINER_NAME="osa"
    REGISTRY_IMAGE="ghcr.io/openscience-collective/osa:latest"
    HOST_PORT=38528
    # Prod uses ROOT_PATH from .env
    ROOT_PATH_OVERRIDE=""
fi

CONTAINER_PORT=38528

# Persistent state: what's been deployed, and whether auto-update is paused
# after a manual rollback.
STATE_DIR="/var/lib/osa/${CONTAINER_NAME}"
DATA_DIR="${STATE_DIR}/data"
DEPLOY_HISTORY_FILE="${STATE_DIR}/deploy-history.log"
ROLLBACK_LOCK_FILE="${STATE_DIR}/.rollback-lock"

##### Functions

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

error_exit() {
    log "ERROR: $1"
    exit 1
}

# Acquire lock to prevent concurrent updates
acquire_lock() {
    if [ -f "$LOCK_FILE" ]; then
        LOCK_PID=$(cat "$LOCK_FILE")
        if ps -p "$LOCK_PID" > /dev/null 2>&1; then
            log "Update already in progress (PID: $LOCK_PID)"
            exit 0
        else
            log "Stale lock file found, removing"
            rm -f "$LOCK_FILE"
        fi
    fi
    echo $$ > "$LOCK_FILE"
}

release_lock() {
    rm -f "$LOCK_FILE"
}

# Check if new image is available
check_for_updates() {
    log "Checking for updates..."

    # Get the image digest of the RUNNING container
    RUNNING_IMAGE=$(docker inspect "$CONTAINER_NAME" --format='{{.Image}}' 2>/dev/null || echo "")
    if [ -z "$RUNNING_IMAGE" ]; then
        log "Container $CONTAINER_NAME not running, will deploy fresh"
        RUNNING_DIGEST=""
    else
        RUNNING_DIGEST="$RUNNING_IMAGE"
        log "Running container image: ${RUNNING_DIGEST:0:19}..."
    fi

    # Pull latest image from registry
    log "Pulling latest image from registry: $REGISTRY_IMAGE"
    docker pull "$REGISTRY_IMAGE" > /dev/null 2>&1

    # Get new image digest
    NEW_DIGEST=$(docker inspect "$REGISTRY_IMAGE" --format='{{.Id}}' 2>/dev/null)

    if [ -z "$NEW_DIGEST" ]; then
        error_exit "Failed to pull image from registry"
    fi

    # `.Id` (above) is the local image config hash - fine for the "did the
    # content change" comparison below, but it's not what the registry serves
    # under `image@digest`. Record the repo digest instead, so a later
    # --rollback can actually re-pull this exact image by digest.
    NEW_REPO_DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' "$REGISTRY_IMAGE" 2>/dev/null | sed 's/.*@//')

    log "Latest registry image: ${NEW_DIGEST:0:19}..."

    # Tag the registry image with local name
    docker tag "$REGISTRY_IMAGE" "$IMAGE_NAME"

    if [ "$RUNNING_DIGEST" = "$NEW_DIGEST" ]; then
        log "No update available (container already running latest)"
        return 1
    else
        log "New image available!"
        log "  Running: ${RUNNING_DIGEST:0:19}..."
        log "  Latest:  ${NEW_DIGEST:0:19}..."
        return 0
    fi
}

# Deploy an image. Takes the image reference to run (a tag like
# $REGISTRY_IMAGE for normal updates, or a digest ref like
# "ghcr.io/openscience-collective/osa@sha256:..." for a rollback) so a
# rollback actually runs the pinned image instead of whatever `:latest`
# resolves to locally.
deploy_update() {
    local image_ref="${1:-$REGISTRY_IMAGE}"
    log "Deploying update: ${image_ref}"

    # Find .env file
    ENV_FILE="${SCRIPT_DIR}/../.env"
    if [ ! -f "$ENV_FILE" ]; then
        ENV_FILE="${SCRIPT_DIR}/.env"
    fi

    if [ ! -f "$ENV_FILE" ]; then
        error_exit "No .env file found (checked ${SCRIPT_DIR}/../.env and ${SCRIPT_DIR}/.env). Refusing to deploy a misconfigured container."
    fi
    ENV_ARGS="--env-file ${ENV_FILE}"
    log "Using env file: ${ENV_FILE}"

    # Stop and remove existing container
    log "Stopping existing container..."
    docker stop "$CONTAINER_NAME" 2>/dev/null || true
    docker rm "$CONTAINER_NAME" 2>/dev/null || true

    # Create persistent data directory
    mkdir -p "${DATA_DIR}" 2>/dev/null || true
    log "Data directory: ${DATA_DIR}"

    # Run the new container using the pulled image
    log "Starting new container on port ${HOST_PORT}..."
    # Build environment overrides
    ENV_OVERRIDE=""
    if [ -n "$ROOT_PATH_OVERRIDE" ]; then
        ENV_OVERRIDE="-e ROOT_PATH=${ROOT_PATH_OVERRIDE}"
    fi

    docker run -d \
        --name "$CONTAINER_NAME" \
        --restart unless-stopped \
        -p "127.0.0.1:${HOST_PORT}:${CONTAINER_PORT}" \
        ${ENV_ARGS} \
        ${ENV_OVERRIDE} \
        -v /var/log/osa:/var/log/osa \
        -v "${DATA_DIR}:/app/data" \
        "$image_ref"

    if [ $? -eq 0 ]; then
        log "Container started successfully"

        # Wait for health check
        log "Waiting for container to be healthy..."
        for i in {1..30}; do
            if docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_NAME" 2>/dev/null | grep -q "healthy"; then
                log "Container is healthy"
                return 0
            fi
            sleep 2
        done
        log "Warning: Container did not become healthy within timeout, but it's running"
        return 0
    else
        error_exit "Failed to start container"
    fi
}

# Cleanup old Docker images
cleanup_old_images() {
    log "Cleaning up old images..."
    docker image prune -f --filter "dangling=true" > /dev/null 2>&1
    log "Cleanup complete"
}

# Send notification (optional)
send_notification() {
    MESSAGE="$1"
    log "NOTIFICATION: $MESSAGE"
}

# Record a successfully-deployed digest, so a later --rollback has a target.
# Only call this after deploy_update has returned successfully.
record_deployment() {
    local digest="$1"
    mkdir -p "$STATE_DIR" 2>/dev/null || true
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') ${digest}" >> "$DEPLOY_HISTORY_FILE"
}

# Print the digest deployed `steps` deployments before the current one.
rollback_target_digest() {
    local steps="$1"
    if [ ! -f "$DEPLOY_HISTORY_FILE" ]; then
        error_exit "No deploy history at ${DEPLOY_HISTORY_FILE}; nothing to roll back to."
    fi
    local total_lines target_line
    total_lines=$(wc -l < "$DEPLOY_HISTORY_FILE" | tr -d ' ')
    target_line=$(( total_lines - steps ))
    if [ "$target_line" -lt 1 ]; then
        error_exit "Not enough deploy history to roll back ${steps} step(s) (only ${total_lines} deployment(s) recorded)."
    fi
    sed -n "${target_line}p" "$DEPLOY_HISTORY_FILE" | awk '{print $2}'
}

# Roll back to a prior recorded digest, pinned (not `:latest`), and lock out
# further auto-updates until an operator confirms the registry is fixed and
# runs --clear-rollback-lock. Without the lock, the next hourly check would
# just see the same bad `:latest` and immediately undo the rollback.
do_rollback() {
    local steps="$1"
    log "Rolling back ${steps} deployment(s)..."

    local target_digest
    target_digest=$(rollback_target_digest "$steps")
    if [ -z "$target_digest" ]; then
        error_exit "Could not determine a rollback target digest."
    fi
    log "Rollback target digest: ${target_digest}"

    local digest_ref="${REGISTRY_IMAGE%%:*}@${target_digest}"
    log "Pulling rollback image: ${digest_ref}"
    docker pull "$digest_ref" > /dev/null 2>&1 || error_exit "Failed to pull rollback image ${digest_ref}"
    docker tag "$digest_ref" "$IMAGE_NAME"

    deploy_update "$digest_ref"

    record_deployment "$target_digest"

    mkdir -p "$STATE_DIR" 2>/dev/null || true
    {
        echo "rolled_back_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        echo "rolled_back_to=${target_digest}"
    } > "$ROLLBACK_LOCK_FILE"

    log "Rollback complete. Auto-updates are PAUSED until the registry's :latest is fixed."
    log "Resume with: ${SCRIPT_DIR}/auto-update.sh --clear-rollback-lock --env ${ENVIRONMENT}"

    send_notification "OSA $ENVIRONMENT rolled back to ${target_digest:0:19}... Auto-updates paused."
}

##### Main execution
log "========================================="
log "OSA Auto-Update Check"
log "Environment: $ENVIRONMENT"
log "========================================="

# Acquire lock
acquire_lock
trap release_lock EXIT

if [ "$CLEAR_ROLLBACK_LOCK" = true ]; then
    if [ -f "$ROLLBACK_LOCK_FILE" ]; then
        rm -f "$ROLLBACK_LOCK_FILE"
        log "Rollback lock cleared. Auto-updates will resume on the next check."
    else
        log "No rollback lock was set."
    fi
    release_lock
    exit 0
fi

if [ "$DO_ROLLBACK" = true ]; then
    do_rollback "$ROLLBACK_STEPS"
    cleanup_old_images
    log "========================================="
    log "Rollback completed successfully!"
    log "========================================="
    release_lock
    exit 0
fi

if [ -f "$ROLLBACK_LOCK_FILE" ]; then
    log "Rollback lock is active ($(cat "$ROLLBACK_LOCK_FILE" | tr '\n' ' ')) - skipping auto-update."
    log "Once the registry's :latest is fixed, resume with --clear-rollback-lock."
    release_lock
    exit 0
fi

# Check for updates
if check_for_updates || [ "$FORCE_UPDATE" = true ]; then
    if [ "$CHECK_ONLY" = true ]; then
        log "Check-only mode: Update available but not deploying"
        send_notification "OSA update available for $ENVIRONMENT"
        exit 0
    fi

    # Deploy update
    deploy_update

    # Record what we just deployed, so a future --rollback has a target.
    # NEW_REPO_DIGEST (not NEW_DIGEST) is what's needed here: NEW_DIGEST is
    # the local image config ID, used above only to detect "did this change";
    # it isn't guaranteed to resolve against the registry via `image@digest`.
    if [ -n "$NEW_REPO_DIGEST" ]; then
        record_deployment "$NEW_REPO_DIGEST"
    else
        log "Warning: could not resolve a repo digest for this deployment; it won't be a --rollback target."
    fi

    # Cleanup
    cleanup_old_images

    # Send success notification
    send_notification "OSA $ENVIRONMENT successfully updated"

    log "========================================="
    log "Update completed successfully!"
    log "========================================="
else
    log "No updates needed"
fi

release_lock

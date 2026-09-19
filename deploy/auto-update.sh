#!/bin/bash

# auto-update.sh - Automatic Docker image update script for OSA
# This script checks for new Docker images and automatically updates the running container
#
# Usage:
#   ./auto-update.sh [options]
#
# Options:
#   --check-only            Only check for updates, don't deploy. Combined with
#                           --rollback, resolves and prints the rollback target
#                           without deploying it.
#   --force                 Force update even if no new image available. Does
#                           NOT override an active rollback lock - run
#                           --clear-rollback-lock first if you actually want to
#                           resume auto-updates after a rollback.
#   --env ENV               Environment (prod|dev), default: prod
#   --rollback [N]          Redeploy the image from N deployments before the one
#                           you're currently on (default: 1). Pins to that
#                           image's digest (not `:latest`) and writes a rollback
#                           lock, so hourly auto-updates won't immediately
#                           re-pull the bad `:latest` and undo the rollback.
#                           Safe to repeat: each successive --rollback during
#                           the same incident (i.e. before --clear-rollback-lock)
#                           walks further back in time rather than bouncing
#                           between the last two images.
#   --clear-rollback-lock   Resume normal auto-updates after a rollback, once the
#                           registry's `:latest` has actually been fixed. Records
#                           the rolled-back-to image as the new deploy-history
#                           tip, so a future rollback starts counting from here.
#
# Setup as cron job (check every hour):
#   0 * * * * /path/to/deploy/auto-update.sh >> /var/log/osa/auto-update.log 2>&1

##### Configuration
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="${LOG_FILE:-/var/log/osa/auto-update.log}"

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

# Scoped per container: prod and dev run through this same script
# concurrently, and sharing one lock file would let a routine dev auto-update
# silently swallow (exit 0, indistinguishable from "nothing to do") an
# operator's emergency prod --rollback if the two happened to overlap.
LOCK_FILE="/tmp/osa-update-${CONTAINER_NAME}.lock"

# Persistent state: what's been deployed, and whether auto-update is paused
# after a manual rollback.
STATE_DIR="/var/lib/osa/${CONTAINER_NAME}"
DATA_DIR="${STATE_DIR}/data"
# Append-only log of genuine deployments (one line per real `docker run` with
# a new image), used to compute "N deployments ago" for --rollback. A
# rollback does NOT append here - see do_rollback - so repeated rollbacks
# during one incident keep walking further back instead of bouncing between
# the last two images. It's re-synced to reality by --clear-rollback-lock.
# Unbounded growth is an accepted limitation: one line per real deploy, so it
# stays small for a very long time; rotate manually if that ever changes.
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
    local pull_output pull_status
    pull_output=$(docker pull "$REGISTRY_IMAGE" 2>&1)
    pull_status=$?
    echo "$pull_output" | tee -a "$LOG_FILE" > /dev/null
    if [ "$pull_status" -ne 0 ]; then
        error_exit "docker pull failed for $REGISTRY_IMAGE"
    fi

    # Get new image digest
    NEW_DIGEST=$(docker inspect "$REGISTRY_IMAGE" --format='{{.Id}}' 2>/dev/null)

    if [ -z "$NEW_DIGEST" ]; then
        error_exit "Failed to pull image from registry"
    fi

    # `.Id` (above) is the local image config hash - fine for the "did the
    # content change" comparison below, but it's not what the registry serves
    # under `image@digest`. Parse the repo digest from this specific pull's
    # own output instead of `docker inspect --format='{{index .RepoDigests
    # 0}}'`: RepoDigests belongs to the local image OBJECT, not the tag we
    # just pulled, so if this host ever pulls two tags (:latest and :dev)
    # that happen to resolve to the same image ID, `index 0` has no
    # guaranteed correspondence to "the tag I just inspected". The `docker
    # pull` output's "Digest: sha256:..." line is unambiguously this pull's.
    NEW_REPO_DIGEST=$(echo "$pull_output" | grep -oE 'Digest: sha256:[0-9a-f]+' | sed 's/Digest: //')

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
    # Set as a script-global (not `local`) so callers can check it after this
    # function returns - deploy_update itself still returns 0 either way
    # (matching its pre-existing contract: a health-check timeout is a
    # warning, not a hard failure), but a rollback caller needs to know which
    # one happened before it reports success.
    DEPLOY_HEALTHY=false

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
                DEPLOY_HEALTHY=true
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

# Print the digest deployed `offset` deployments before the tip of
# DEPLOY_HISTORY_FILE. Writes any error to stderr and returns 1 - NOT
# error_exit (log + `exit`) - because this is always called via `$(...)`
# command substitution, which runs it in a subshell: `exit` there would only
# terminate the subshell, and the error text written to stdout would get
# silently captured as the "digest" instead of actually stopping the script.
rollback_target_digest() {
    local offset="$1"
    if [ ! -f "$DEPLOY_HISTORY_FILE" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: No deploy history at ${DEPLOY_HISTORY_FILE}; nothing to roll back to." | tee -a "$LOG_FILE" >&2
        return 1
    fi
    local total_lines target_line
    total_lines=$(wc -l < "$DEPLOY_HISTORY_FILE" | tr -d ' ')
    target_line=$(( total_lines - offset ))
    if [ "$target_line" -lt 1 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Not enough deploy history to roll back ${offset} step(s) (only ${total_lines} deployment(s) recorded)." | tee -a "$LOG_FILE" >&2
        return 1
    fi
    sed -n "${target_line}p" "$DEPLOY_HISTORY_FILE" | awk '{print $2}'
}

# Roll back to a prior recorded digest, pinned (not `:latest`), and lock out
# further auto-updates until an operator confirms the registry is fixed and
# runs --clear-rollback-lock. Without the lock, the next hourly check would
# just see the same bad `:latest` and immediately undo the rollback.
#
# Deliberately does NOT append the rollback target to DEPLOY_HISTORY_FILE.
# If it did, "N deployments ago" would be measured against a history that
# includes the rollback itself, so a second `--rollback 1` during the same
# incident would land back on the very image just escaped (history
# [d1,d2,d3], roll back to d2 and append it -> [d1,d2,d3,d2], and "1 back
# from here" is d3 again) instead of walking further into the past. Instead,
# the cumulative offset already rolled back this incident is tracked in
# ROLLBACK_LOCK_FILE itself and added to each new request, so repeated
# --rollback calls keep advancing through the untouched, real deploy
# history. --clear-rollback-lock is what reconciles history with reality
# once the incident is over.
do_rollback() {
    local requested_steps="$1"
    if [ "$requested_steps" -lt 1 ]; then
        error_exit "--rollback requires a positive step count, got ${requested_steps}."
    fi

    local base_offset=0
    if [ -f "$ROLLBACK_LOCK_FILE" ]; then
        base_offset=$(grep '^rolled_back_offset=' "$ROLLBACK_LOCK_FILE" 2>/dev/null | cut -d= -f2)
        base_offset="${base_offset:-0}"
        log "Continuing an active rollback (already ${base_offset} step(s) back this incident)."
    fi
    local total_offset=$(( base_offset + requested_steps ))
    log "Rolling back ${requested_steps} more deployment(s) (${total_offset} total this incident)..."

    local target_digest
    target_digest=$(rollback_target_digest "$total_offset") || error_exit "Could not determine a rollback target digest."
    log "Rollback target digest: ${target_digest}"

    local digest_ref="${REGISTRY_IMAGE%%:*}@${target_digest}"
    log "Pulling rollback image: ${digest_ref}"
    docker pull "$digest_ref" > /dev/null 2>&1 || error_exit "Failed to pull rollback image ${digest_ref}"
    docker tag "$digest_ref" "$IMAGE_NAME"

    deploy_update "$digest_ref"

    mkdir -p "$STATE_DIR" 2>/dev/null || true
    {
        echo "rolled_back_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        echo "rolled_back_to=${target_digest}"
        echo "rolled_back_offset=${total_offset}"
        echo "healthy=${DEPLOY_HEALTHY}"
    } > "$ROLLBACK_LOCK_FILE"

    if [ "$DEPLOY_HEALTHY" = true ]; then
        log "Rollback complete. Auto-updates are PAUSED until the registry's :latest is fixed."
        log "Resume with: ${SCRIPT_DIR}/auto-update.sh --clear-rollback-lock --env ${ENVIRONMENT}"
        send_notification "OSA $ENVIRONMENT rolled back to ${target_digest:0:19}... Auto-updates paused."
    else
        log "UNHEALTHY: rollback target ${target_digest:0:19}... was deployed but never reported healthy."
        log "This rollback did NOT resolve the incident - investigate manually. Auto-updates remain PAUSED."
        send_notification "OSA $ENVIRONMENT rollback to ${target_digest:0:19}... deployed but UNHEALTHY. Manual investigation needed."
    fi
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
        # Reconcile: record what we're actually running as the new deploy-
        # history tip, so a future --rollback's "N deployments ago" counts
        # from here rather than from before this incident.
        ROLLED_BACK_TO=$(grep '^rolled_back_to=' "$ROLLBACK_LOCK_FILE" 2>/dev/null | cut -d= -f2)
        if [ -n "$ROLLED_BACK_TO" ]; then
            record_deployment "$ROLLED_BACK_TO"
            log "Recorded ${ROLLED_BACK_TO:0:19}... as the current deploy-history tip."
        fi
        rm -f "$ROLLBACK_LOCK_FILE"
        log "Rollback lock cleared. Auto-updates will resume on the next check."
    else
        log "No rollback lock was set."
    fi
    release_lock
    exit 0
fi

if [ "$DO_ROLLBACK" = true ]; then
    if [ "$CHECK_ONLY" = true ]; then
        BASE_OFFSET=0
        if [ -f "$ROLLBACK_LOCK_FILE" ]; then
            BASE_OFFSET=$(grep '^rolled_back_offset=' "$ROLLBACK_LOCK_FILE" 2>/dev/null | cut -d= -f2)
            BASE_OFFSET="${BASE_OFFSET:-0}"
        fi
        TOTAL_OFFSET=$(( BASE_OFFSET + ROLLBACK_STEPS ))
        CHECK_TARGET=$(rollback_target_digest "$TOTAL_OFFSET") || error_exit "Could not determine a rollback target digest."
        log "Check-only mode: --rollback ${ROLLBACK_STEPS} would deploy ${CHECK_TARGET} (not deploying)."
        release_lock
        exit 0
    fi
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
    log "--force does not override this lock. Once the registry's :latest is fixed, resume with --clear-rollback-lock."
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

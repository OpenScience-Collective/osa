#!/bin/bash

# deploy.sh - Pull and deploy OSA Docker container from GHCR
# Usage: ./deploy.sh [environment]
# Environment: 'prod' (default) or 'dev'

##### Constants
ENVIRONMENT="${1:-prod}"
DEPLOY_DIR=$(cd "$(dirname "$0")/.." && pwd)

# Set environment-specific variables
# Port allocation: HEDit prod=38427, HEDit dev=38428, OSA prod=38528, OSA dev=38529
if [ "$ENVIRONMENT" = "dev" ]; then
    REGISTRY_IMAGE="ghcr.io/openscience-collective/osa:dev"
    CONTAINER_NAME="osa-dev"
    HOST_PORT=38529
    # Dev uses DEV_ROOT_PATH, defaults to /osa-dev
    ROOT_PATH_OVERRIDE="${DEV_ROOT_PATH:-/osa-dev}"
else
    REGISTRY_IMAGE="ghcr.io/openscience-collective/osa:latest"
    CONTAINER_NAME="osa"
    HOST_PORT=38528
    # Prod uses ROOT_PATH from .env
    ROOT_PATH_OVERRIDE=""
fi

CONTAINER_PORT=38528

##### Functions

error_exit() {
    echo "[ERROR] $1"
    exit 1
}

pull_image() {
    echo "Pulling image from registry: ${REGISTRY_IMAGE}..."
    docker pull "${REGISTRY_IMAGE}" || error_exit "Failed to pull image"
}

stop_existing_container() {
    echo "Stopping existing container ${CONTAINER_NAME}..."
    docker stop "${CONTAINER_NAME}" 2>/dev/null || true
    docker rm "${CONTAINER_NAME}" 2>/dev/null || true
}

run_container() {
    echo "Starting container ${CONTAINER_NAME} on port ${HOST_PORT}..."

    ENV_FILE="${DEPLOY_DIR}/.env"
    ENV_ARGS=""
    if [ -f "$ENV_FILE" ]; then
        ENV_ARGS="--env-file ${ENV_FILE}"
    fi

    # Create persistent data directory on host
    DATA_DIR="/var/lib/osa/${CONTAINER_NAME}/data"
    mkdir -p "${DATA_DIR}" 2>/dev/null || \
        echo "Warning: Could not create ${DATA_DIR}, data may not persist"

    # Build environment overrides
    ENV_OVERRIDE=""
    if [ -n "$ROOT_PATH_OVERRIDE" ]; then
        ENV_OVERRIDE="-e ROOT_PATH=${ROOT_PATH_OVERRIDE}"
    fi

    docker run -d \
        --name "${CONTAINER_NAME}" \
        --restart unless-stopped \
        -p "127.0.0.1:${HOST_PORT}:${CONTAINER_PORT}" \
        -v "${DATA_DIR}:/app/data" \
        ${ENV_ARGS} \
        ${ENV_OVERRIDE} \
        "${REGISTRY_IMAGE}" || error_exit "Failed to start container"
}

wait_for_health() {
    echo "Waiting for container to be healthy..."
    for i in {1..30}; do
        if curl -sf "http://localhost:${HOST_PORT}/health" > /dev/null 2>&1; then
            echo "Container is healthy!"
            return 0
        fi
        sleep 2
    done
    echo "Warning: Container did not become healthy within timeout"
    return 1
}

##### Main
echo "========================================="
echo "OSA Deployment (GHCR)"
echo "========================================="
echo "Environment: ${ENVIRONMENT}"
echo "Image: ${REGISTRY_IMAGE}"
echo "Container: ${CONTAINER_NAME}"
echo "Port: 127.0.0.1:${HOST_PORT}"
echo "========================================="

pull_image
stop_existing_container
run_container
sleep 3
wait_for_health

echo ""
echo "Deployment complete!"
echo "Health: http://localhost:${HOST_PORT}/health"
echo "Logs:   docker logs -f ${CONTAINER_NAME}"

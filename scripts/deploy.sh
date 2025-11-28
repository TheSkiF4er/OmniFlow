#!/bin/bash
# OmniFlow Deployment Script
# Description: Deploy OmniFlow using Docker Compose
# Usage: ./deploy.sh [environment]
# Example: ./deploy.sh dev

set -e

# Default environment
ENVIRONMENT=${1:-dev}

echo "=========================================="
echo " OmniFlow Deployment Script"
echo " Environment: $ENVIRONMENT"
echo "=========================================="

# Step 1: Pull latest Docker images
echo "Pulling latest Docker images..."
docker-compose -f docker/docker-compose.yml pull

# Step 2: Build local images if necessary
echo "Building local Docker images..."
docker-compose -f docker/docker-compose.yml build

# Step 3: Start containers
echo "Starting OmniFlow services..."
docker-compose -f docker/docker-compose.yml up -d

# Step 4: Display running containers
echo "Currently running OmniFlow containers:"
docker ps --filter "name=omniflow"

echo "=========================================="
echo " Deployment completed successfully!"
echo " Access UI at http://localhost:3000"
echo " Core API at http://localhost:8080"
echo "=========================================="

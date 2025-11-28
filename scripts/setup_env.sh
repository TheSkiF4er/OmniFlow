#!/bin/bash
# OmniFlow Environment Setup Script
# Description: Setup environment variables and configuration files for OmniFlow
# Usage: ./setup_env.sh

set -e

echo "=========================================="
echo " OmniFlow Environment Setup Script"
echo "=========================================="

# Step 1: Create .env file
ENV_FILE=".env"

echo "Creating environment file at $ENV_FILE..."
cat > $ENV_FILE <<EOL
# OmniFlow Environment Variables

# Core settings
OMNIFLOW_ENV=development
CORE_PORT=8080

# Database settings
DB_HOST=localhost
DB_PORT=5432
DB_USER=omniflow
DB_PASSWORD=example
DB_NAME=omniflow_dev

# Redis settings
REDIS_HOST=localhost
REDIS_PORT=6379

# Logging
LOG_LEVEL=DEBUG

EOL

echo ".env file created successfully!"

# Step 2: Create directories for plugins and logs
echo "Creating directories..."
mkdir -p plugins/logs
mkdir -p plugins/tmp

echo "Directories created: plugins/logs, plugins/tmp"

# Step 3: Instructions for the user
echo "=========================================="
echo " Environment setup completed!"
echo " You can now run OmniFlow using Docker or CLI:"
echo " - docker-compose up -d"
echo " - ./scripts/deploy.sh"
echo "=========================================="

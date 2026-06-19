#!/bin/bash
# clean_workspace.sh
# removes the entire workspace so the pipeline runs from scratch

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "cleaning entire workspace"
rm -rf "$PROJECT_DIR/workspace"

echo "removing docker images if they exist"
docker rmi llmbreakguard-pre-0      2>/dev/null || true
docker rmi llmbreakguard-breaking-0 2>/dev/null || true
docker rmi llmbreakguard-base:latest 2>/dev/null || true

echo "workspace cleaned"
echo "run: bash scripts/run_pipeline.sh"
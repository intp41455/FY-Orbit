#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="${1:-find-yourself}"
BRANCH="${2:-feat/package-a}"
API_BASE_URL="${3:-${VITE_API_BASE_URL:-}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WEB_DIR="${REPO_ROOT}/web"

echo "==> [1/3] Building Web frontend..."
cd "${WEB_DIR}"
if [ -n "${API_BASE_URL}" ]; then
  export VITE_API_BASE_URL="${API_BASE_URL}"
  echo "    Injected VITE_API_BASE_URL: ${API_BASE_URL}"
fi
npm run build

echo "==> [2/3] Checking dist output..."
if [ ! -d "${WEB_DIR}/dist" ]; then
  echo "Error: dist directory not found at ${WEB_DIR}/dist" >&2
  exit 1
fi

echo "==> [3/3] Deploying to Cloudflare Pages via Wrangler..."
npx wrangler pages deploy dist --project-name "${PROJECT_NAME}" --branch "${BRANCH}"
echo "==> Deployment completed successfully!"

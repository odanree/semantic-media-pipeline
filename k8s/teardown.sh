#!/usr/bin/env bash
# =============================================================================
# Lumen - Teardown / Uninstall Script
# Removes all Lumen resources from the Kubernetes cluster
# =============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

NAMESPACE="lumen"
K8S_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
log_warn "This will DELETE all Lumen resources in namespace '${NAMESPACE}'!"
read -r -p "Continue? (y/N): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "Aborted."
    exit 0
fi

echo ""
log_info "Removing Lumen application manifests..."
kubectl delete -f "${K8S_DIR}/hpa.yaml" --ignore-not-found -n "${NAMESPACE}"
kubectl delete -f "${K8S_DIR}/network-policies.yaml" --ignore-not-found -n "${NAMESPACE}"
kubectl delete -f "${K8S_DIR}/flower-deployment.yaml" --ignore-not-found -n "${NAMESPACE}"
kubectl delete -f "${K8S_DIR}/frontend-deployment.yaml" --ignore-not-found -n "${NAMESPACE}"
kubectl delete -f "${K8S_DIR}/api-deployment.yaml" --ignore-not-found -n "${NAMESPACE}"
kubectl delete -f "${K8S_DIR}/worker-deployment.yaml" --ignore-not-found -n "${NAMESPACE}"
kubectl delete -f "${K8S_DIR}/pvc.yaml" --ignore-not-found -n "${NAMESPACE}"
kubectl delete -f "${K8S_DIR}/configmap.yaml" --ignore-not-found -n "${NAMESPACE}"
kubectl delete -f "${K8S_DIR}/secrets.yaml" --ignore-not-found -n "${NAMESPACE}"
kubectl delete -f "${K8S_DIR}/initdb-configmap.yaml" --ignore-not-found -n "${NAMESPACE}"
log_ok "Application manifests removed"

echo ""
log_info "Uninstalling Helm releases..."
helm uninstall qdrant -n "${NAMESPACE}" 2>/dev/null || true
helm uninstall postgresql -n "${NAMESPACE}" 2>/dev/null || true
helm uninstall redis -n "${NAMESPACE}" 2>/dev/null || true
log_ok "Helm releases removed"

echo ""
log_info "Removing namespace..."
kubectl delete namespace "${NAMESPACE}" --ignore-not-found
log_ok "Namespace '${NAMESPACE}' deleted"

echo ""
log_ok "Lumen teardown complete."

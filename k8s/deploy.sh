#!/usr/bin/env bash
# =============================================================================
# Lumen Kubernetes Deployment Script
# Deploys the full Lumen stack to a Kubernetes cluster
# =============================================================================
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_err() { echo -e "${RED}[ERROR]${NC} $1"; }

# =============================================================================
# Configuration
# =============================================================================
NAMESPACE="lumen"
K8S_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_PASSWORD="${DATABASE_PASSWORD:-$(openssl rand -base64 24)}"
REGISTRY="${REGISTRY:-ghcr.io/your-org}"   # Set your container registry

log_info "=== Lumen Kubernetes Deployment ==="
log_info "Namespace: ${NAMESPACE}"
log_info "Registry: ${REGISTRY}"
echo ""

# =============================================================================
# Prerequisites Check
# =============================================================================
log_info "Checking prerequisites..."

if ! command -v kubectl &> /dev/null; then
    log_err "kubectl not found. Install: https://kubernetes.io/docs/tasks/tools/"
    exit 1
fi

if ! command -v helm &> /dev/null; then
    log_err "helm not found. Install: https://helm.sh/docs/intro/install/"
    exit 1
fi

if ! kubectl cluster-info &> /dev/null; then
    log_err "Cannot connect to Kubernetes cluster. Check your kubeconfig."
    exit 1
fi

log_ok "Prerequisites satisfied"
echo ""

# =============================================================================
# Step 1: Create Namespace
# =============================================================================
log_info "Step 1: Creating namespace..."
kubectl apply -f "${K8S_DIR}/namespace.yaml"
log_ok "Namespace '${NAMESPACE}' created"
echo ""

# =============================================================================
# Step 2: Add Helm Repos
# =============================================================================
log_info "Step 2: Adding Helm repositories..."
helm repo add bitnami https://charts.bitnami.com/bitnami 2>/dev/null || true
helm repo add qdrant https://qdrant.github.io/qdrant-helm 2>/dev/null || true
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia 2>/dev/null || true
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx 2>/dev/null || true
helm repo add jetstack https://charts.jetstack.io 2>/dev/null || true
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts 2>/dev/null || true
helm repo update
log_ok "Helm repos updated"
echo ""

# =============================================================================
# Step 3: Install NVIDIA GPU Operator (if GPU nodes available)
# =============================================================================
if kubectl get nodes -l nvidia.com/gpu.present=true --no-headers 2>/dev/null | grep -q .; then
    log_info "Step 3: GPU nodes detected, installing NVIDIA GPU Operator..."
    helm upgrade --install gpu-operator nvidia/gpu-operator \
        --namespace gpu-operator --create-namespace \
        --set driver.enabled=true \
        --set toolkit.enabled=true \
        --wait --timeout 600s || log_warn "GPU Operator install failed; continuing..."
    log_ok "NVIDIA GPU Operator installed"
else
    log_warn "Step 3: No GPU nodes detected. Skipping NVIDIA GPU Operator."
    log_warn "  Workers will run in CPU-only mode."
fi
echo ""

# =============================================================================
# Step 4: Install Redis
# =============================================================================
log_info "Step 4: Installing Redis..."
helm upgrade --install redis bitnami/redis \
    --namespace "${NAMESPACE}" \
    --set architecture=standalone \
    --set auth.enabled=false \
    --set master.persistence.size=5Gi \
    --set master.resources.requests.cpu=250m \
    --set master.resources.requests.memory=256Mi \
    --wait --timeout 300s
log_ok "Redis installed"
echo ""

# =============================================================================
# Step 5: Install PostgreSQL
# =============================================================================
log_info "Step 5: Installing PostgreSQL..."
helm upgrade --install postgresql bitnami/postgresql \
    --namespace "${NAMESPACE}" \
    --set auth.username=lumen_user \
    --set auth.password="${DB_PASSWORD}" \
    --set auth.database=lumen \
    --set primary.persistence.size=20Gi \
    --set primary.resources.requests.cpu=500m \
    --set primary.resources.requests.memory=512Mi \
    --set metrics.enabled=true \
    --wait --timeout 300s
log_ok "PostgreSQL installed (password stored in secret postgresql)"
echo ""

# =============================================================================
# Step 6: Install Qdrant
# =============================================================================
log_info "Step 6: Installing Qdrant..."
helm upgrade --install qdrant qdrant/qdrant \
    --namespace "${NAMESPACE}" \
    --set replicaCount=1 \
    --set image.tag=v1.17.0 \
    --set persistence.size=50Gi \
    --set resources.requests.cpu=500m \
    --set resources.requests.memory=2Gi \
    --wait --timeout 300s
log_ok "Qdrant installed"
echo ""

# =============================================================================
# Step 7: Apply Secrets and ConfigMap
# =============================================================================
log_info "Step 7: Applying ConfigMap and Secrets..."

# Update secret with generated password
DB_PASSWORD_B64=$(echo -n "${DB_PASSWORD}" | base64)
sed "s|c2VjdXJlX3Bhc3N3b3JkX2hlcmU=|${DB_PASSWORD_B64}|g" \
    "${K8S_DIR}/secrets.yaml" | kubectl apply -f -

kubectl apply -f "${K8S_DIR}/configmap.yaml"
log_ok "ConfigMap and Secrets applied"
echo ""

# =============================================================================
# Step 8: Apply PVCs
# =============================================================================
log_info "Step 8: Creating PersistentVolumeClaims..."
kubectl apply -f "${K8S_DIR}/pvc.yaml"
log_ok "PVCs created"
echo ""

# =============================================================================
# Step 9: Deploy Lumen Services
# =============================================================================
log_info "Step 9: Deploying Lumen application..."
kubectl apply -f "${K8S_DIR}/worker-deployment.yaml"
kubectl apply -f "${K8S_DIR}/api-deployment.yaml"
kubectl apply -f "${K8S_DIR}/frontend-deployment.yaml"
kubectl apply -f "${K8S_DIR}/flower-deployment.yaml"
log_ok "All deployments applied"
echo ""

# =============================================================================
# Step 10: Apply HPAs and Network Policies
# =============================================================================
log_info "Step 10: Applying autoscaling and network policies..."
kubectl apply -f "${K8S_DIR}/hpa.yaml"
kubectl apply -f "${K8S_DIR}/network-policies.yaml"
log_ok "HPAs and NetworkPolicies applied"
echo ""

# =============================================================================
# Step 11: Install Ingress Controller (if not present)
# =============================================================================
if ! kubectl get ns ingress-nginx &>/dev/null; then
    log_info "Step 11: Installing Ingress NGINX controller..."
    helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
        --namespace ingress-nginx --create-namespace \
        --set controller.replicaCount=2 \
        --wait --timeout 300s
    log_ok "Ingress NGINX installed"
else
    log_ok "Step 11: Ingress NGINX already installed"
fi
echo ""

# =============================================================================
# Summary
# =============================================================================
echo ""
log_info "=============================================="
log_info "  Lumen Deployment Complete!"
log_info "=============================================="
echo ""
log_info "Services:"
echo "  API:       kubectl port-forward svc/lumen-api 8000:8000 -n ${NAMESPACE}"
echo "  Frontend:  kubectl port-forward svc/lumen-frontend 3000:3000 -n ${NAMESPACE}"
echo "  Flower:    kubectl port-forward svc/lumen-flower 5555:5555 -n ${NAMESPACE}"
echo "  Qdrant:    kubectl port-forward svc/qdrant 6333:6333 -n ${NAMESPACE}"
echo ""
log_info "Verify:"
echo "  kubectl get pods -n ${NAMESPACE}"
echo "  kubectl get svc -n ${NAMESPACE}"
echo "  kubectl get hpa -n ${NAMESPACE}"
echo ""
log_info "Database password: ${DB_PASSWORD}"
log_warn "  Save this password! It cannot be recovered."
echo ""
log_info "Scale workers:"
echo "  kubectl scale deployment/lumen-worker --replicas=5 -n ${NAMESPACE}"
echo ""

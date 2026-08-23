#!/usr/bin/env bash
# Install all ASAP helm charts into a Kubernetes cluster.
# Compatible with both bash and zsh.
#
# Usage:
#   ./scripts/install-charts.sh \
#     --neo4j-password         '<password>'  \
#     --pg-user                '<user>'      \
#     --pg-password            '<password>'  \
#     --aspace-user            '<username>'  \
#     --aspace-password        '<password>'  \
#     --inference-api-key      '<key>'       \  # use 'ollama' for Mode 1 (in-cluster Ollama)
#     --keycloak-admin-password '<password>' \  # Keycloak master admin password
#     [--inference-base-url '<url>']         \  # override for Mode 2 (external server)
#     [--inference-model '<model>']          \  # override model name
#     [--inference-reasoning-effort '<effort>'] \  # 'none' REQUIRED for OpenAI gpt-5-class + tools on chat completions; omit for LM Studio/gpt-oss
#     [--inference-min-p '<0..1>']            \  # llama.cpp/LM Studio/vLLM extension; set 0 for api.openai.com (rejects it)
#     [--inference-temperature '<t>']         \  # -1 omits the parameter (required for OpenAI reasoning-class models)
#     [--inference-context-window-tokens '<n>'] \  # model context size, drives the UI usage percentage
#     [--asap-env development|production]    \
#     [--namespace asap]                     \
#     [--skip-ollama]                        \
#     [--skip-extractor]                     \
#     [--ingress-host asapbackend.localhost]     \  # DEPRECATED, ignored (backend has no direct ingress)
#     [--ui-host asapui.localhost]               \  # enable UI ingress (.localhost resolves natively)
#     [--neo4j-host neo4j.localhost]             \  # enable Neo4j browser ingress (.localhost resolves natively)
#     [--keycloak-host keycloak.localhost]          # enable Keycloak ingress (.localhost resolves natively)

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
NAMESPACE="asap"
CLUSTER_NAME="asap"
SKIP_OLLAMA=false
SKIP_EXTRACTOR=false

NEO4J_PASSWORD=""
PG_USER=""
PG_PASSWORD=""
ASPACE_USER=""
ASPACE_PASSWORD=""
INFERENCE_API_KEY=""
INFERENCE_BASE_URL=""
INFERENCE_MODEL=""
INFERENCE_REASONING_EFFORT=""
INFERENCE_MIN_P=""
INFERENCE_TEMPERATURE=""
INFERENCE_CONTEXT_WINDOW_TOKENS=""
ASAP_ENV=""
INGRESS_HOST=""
UI_HOST=""
NEO4J_HOST=""
KEYCLOAK_ADMIN_PASSWORD=""
KEYCLOAK_HOST=""

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --neo4j-password)  NEO4J_PASSWORD="$2";  shift 2 ;;
    --pg-user)         PG_USER="$2";         shift 2 ;;
    --pg-password)     PG_PASSWORD="$2";     shift 2 ;;
    --aspace-user)     ASPACE_USER="$2";     shift 2 ;;
    --aspace-password)    ASPACE_PASSWORD="$2";    shift 2 ;;
    --inference-api-key)  INFERENCE_API_KEY="$2";  shift 2 ;;
    --inference-base-url) INFERENCE_BASE_URL="$2"; shift 2 ;;
    --inference-model)    INFERENCE_MODEL="$2";    shift 2 ;;
    --inference-reasoning-effort) INFERENCE_REASONING_EFFORT="$2"; shift 2 ;;
    --inference-min-p)            INFERENCE_MIN_P="$2";            shift 2 ;;
    --inference-temperature)      INFERENCE_TEMPERATURE="$2";      shift 2 ;;
    --inference-context-window-tokens) INFERENCE_CONTEXT_WINDOW_TOKENS="$2"; shift 2 ;;
    --asap-env)           ASAP_ENV="$2";           shift 2 ;;
    --ingress-host)       INGRESS_HOST="$2";       shift 2 ;;
    --ui-host)            UI_HOST="$2";            shift 2 ;;
    --neo4j-host)              NEO4J_HOST="$2";              shift 2 ;;
    --keycloak-admin-password) KEYCLOAK_ADMIN_PASSWORD="$2"; shift 2 ;;
    --keycloak-host)           KEYCLOAK_HOST="$2";           shift 2 ;;
    --namespace)          NAMESPACE="$2";          shift 2 ;;
    --skip-ollama)        SKIP_OLLAMA=true;        shift   ;;
    --skip-extractor)     SKIP_EXTRACTOR=true;     shift   ;;
    *)
      echo "Error: unknown argument '$1'"
      echo "Run with --help to see usage."
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
MISSING=()
[[ -z "$NEO4J_PASSWORD"   ]] && MISSING+=("--neo4j-password")
[[ -z "$PG_USER"          ]] && MISSING+=("--pg-user")
[[ -z "$PG_PASSWORD"      ]] && MISSING+=("--pg-password")
[[ -z "$ASPACE_USER"      ]] && MISSING+=("--aspace-user")
[[ -z "$ASPACE_PASSWORD"  ]] && MISSING+=("--aspace-password")
[[ -z "$INFERENCE_API_KEY"        ]] && MISSING+=("--inference-api-key")
[[ -z "$KEYCLOAK_ADMIN_PASSWORD"  ]] && MISSING+=("--keycloak-admin-password")

if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "Error: the following required arguments are missing:"
  for arg in "${MISSING[@]}"; do
    echo "  $arg"
  done
  exit 1
fi

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HELM_DIR="$WORKSPACE_ROOT/helm"

# ---------------------------------------------------------------------------
# Uninstall existing releases (clean reinstall)
# Uninstalled in reverse dependency order; silently skipped if not present.
# ---------------------------------------------------------------------------
echo ""
echo "Uninstalling existing ASAP helm releases from namespace '${NAMESPACE}'..."

# Jobs triggered at runtime by asapbackend (extractor runs, document ingestion)
# are API-created, not helm-owned — helm uninstall never sees them. Remove
# them here so redeploys start clean; the suspended template jobs are
# chart-owned and get recreated by the installs below.
echo "  ▶ Deleting runtime-triggered jobs (extractor + docworker runs)..."
kubectl delete jobs --namespace "$NAMESPACE" -l triggered-by=asapbackend --ignore-not-found 2>/dev/null || true

for release in asapui asapbackend neo4j-mcp vllm postgres neo4j keycloak; do
  if helm status "$release" --namespace "$NAMESPACE" > /dev/null 2>&1; then
    echo "  ▶ Uninstalling ${release}..."
    helm uninstall "$release" --namespace "$NAMESPACE" --wait
    echo "  ✓ ${release} uninstalled."
  else
    echo "  — ${release} not installed, skipping."
  fi
done

if [[ "$SKIP_OLLAMA" == false ]]; then
  if helm status ollama --namespace "$NAMESPACE" > /dev/null 2>&1; then
    echo "  ▶ Uninstalling ollama..."
    helm uninstall ollama --namespace "$NAMESPACE" --wait
    echo "  ✓ ollama uninstalled."
  else
    echo "  — ollama not installed, skipping."
  fi
else
  echo "  — ollama skipped (--skip-ollama set)."
fi

if [[ "$SKIP_EXTRACTOR" == false ]]; then
  if helm status asapextractor --namespace "$NAMESPACE" > /dev/null 2>&1; then
    echo "  ▶ Uninstalling asapextractor..."
    helm uninstall asapextractor --namespace "$NAMESPACE" --wait
    echo "  ✓ asapextractor uninstalled."
  else
    echo "  — asapextractor not installed, skipping."
  fi
else
  echo "  — asapextractor skipped (--skip-extractor set)."
fi

if helm status kggenerator --namespace "$NAMESPACE" > /dev/null 2>&1; then
  echo "  ▶ Uninstalling kggenerator..."
  helm uninstall kggenerator --namespace "$NAMESPACE" --wait
  echo "  ✓ kggenerator uninstalled."
else
  echo "  — kggenerator not installed, skipping."
fi

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------
echo ""
echo "Installing ASAP helm charts into namespace '${NAMESPACE}'..."
echo ""

# Create namespace if it doesn't already exist.
kubectl get namespace "$NAMESPACE" > /dev/null 2>&1 \
  || kubectl create namespace "$NAMESPACE"

# ---------------------------------------------------------------------------
# Register host.k3d.internal in CoreDNS
# On macOS, Docker Desktop provides host.docker.internal which always resolves
# to the Mac host regardless of which WiFi network you're on. We resolve its
# IP once and register it as host.k3d.internal so all ASAP configs use a
# consistent name.
# ---------------------------------------------------------------------------
echo "▶ Registering host.k3d.internal in CoreDNS..."
DOCKER_HOST_IP=$(docker run --rm alpine getent ahostsv4 host.docker.internal \
  2>/dev/null | awk '{print $1}' | head -1)
if [[ -z "$DOCKER_HOST_IP" ]]; then
  echo "  Warning: could not resolve host.docker.internal — skipping CoreDNS patch."
  echo "  Set --inference-base-url manually with your Mac's current IP."
else
  CURRENT_HOSTS=$(kubectl get configmap coredns -n kube-system \
    -o jsonpath='{.data.NodeHosts}')
  NEW_HOSTS=$(printf '%s' "$CURRENT_HOSTS" \
    | grep -v "host.k3d.internal")$'\n'"${DOCKER_HOST_IP} host.k3d.internal"
  kubectl patch configmap coredns -n kube-system \
    --type merge \
    -p "{\"data\":{\"NodeHosts\":$(printf '%s' "$NEW_HOSTS" | jq -Rs .)}}"
  kubectl rollout restart deployment coredns -n kube-system > /dev/null
  kubectl rollout status deployment coredns -n kube-system --timeout=30s > /dev/null
  echo "✓ host.k3d.internal → ${DOCKER_HOST_IP}"
fi
echo ""

# Install TLS secret (no sudo — CA trust store installation is a one-time step
# done separately via: ./scripts/setup-tls.sh --install-ca)
echo "▶ Installing TLS secret..."
"$SCRIPT_DIR/setup-tls.sh" --namespace "$NAMESPACE"
echo ""

echo "▶ Installing keycloak..."
KEYCLOAK_SETS="--set keycloak.adminPassword=${KEYCLOAK_ADMIN_PASSWORD}"
KEYCLOAK_SETS="$KEYCLOAK_SETS --set postgres.password=${KEYCLOAK_ADMIN_PASSWORD}"
[[ -n "$KEYCLOAK_HOST" ]] && KEYCLOAK_SETS="$KEYCLOAK_SETS --set ingress.enabled=true --set ingress.host=${KEYCLOAK_HOST}"
# shellcheck disable=SC2086
helm upgrade --install keycloak "$HELM_DIR/keycloak" \
  --namespace "$NAMESPACE" \
  $KEYCLOAK_SETS
echo "✓ keycloak installed."
echo ""

echo "▶ Installing neo4j..."
NEO4J_SETS=""
[[ -n "$NEO4J_HOST" ]] && NEO4J_SETS="--set ingress.enabled=true --set ingress.host=${NEO4J_HOST}"
helm upgrade --install neo4j "$HELM_DIR/neo4j" \
  --namespace "$NAMESPACE" \
  --set auth.password="$NEO4J_PASSWORD" \
  $NEO4J_SETS
echo "✓ neo4j installed."

echo ""
echo "▶ Installing postgres..."
helm upgrade --install postgres "$HELM_DIR/postgres" \
  --namespace "$NAMESPACE" \
  --set auth.user="$PG_USER" \
  --set auth.password="$PG_PASSWORD"
echo "✓ postgres installed."

echo ""
echo "▶ Installing neo4j-mcp..."
helm upgrade --install neo4j-mcp "$HELM_DIR/neo4j-mcp" \
  --namespace "$NAMESPACE"
echo "✓ neo4j-mcp installed."

echo ""
echo "▶ Installing vllm (embedding server)..."
helm upgrade --install vllm "$HELM_DIR/vllm" \
  --namespace "$NAMESPACE"
echo "✓ vllm installed."
echo "  Note: the pod will not become ready until the model finishes downloading (~2–3 min on first boot)."

echo ""
echo "▶ Installing asapdocworker (document ingestion worker)..."
helm upgrade --install asapdocworker "$HELM_DIR/asapdocworker" \
  --namespace "$NAMESPACE"
echo "✓ asapdocworker installed (Job is suspended — cloned per upload by asapbackend)."

if [[ "$SKIP_OLLAMA" == false ]]; then
  echo ""
  echo "▶ Installing ollama..."
  helm upgrade --install ollama "$HELM_DIR/ollama" \
    --namespace "$NAMESPACE"
  echo "✓ ollama installed."
  echo "  Note: the pod will not become ready until the model finishes downloading."
else
  echo ""
  echo "  Skipping ollama (--skip-ollama set — configure an external inference endpoint in asapbackend)."
fi

if [[ "$SKIP_EXTRACTOR" == false ]]; then
  echo ""
  echo "▶ Installing asapextractor..."
  helm upgrade --install asapextractor "$HELM_DIR/asapextractor" \
    --namespace "$NAMESPACE" \
    --set aspace.username="$ASPACE_USER" \
    --set aspace.password="$ASPACE_PASSWORD"
  echo "✓ asapextractor installed (Job is suspended — trigger via asapbackend)."
else
  echo ""
  echo "  Skipping asapextractor (--skip-extractor set)."
fi

echo ""
echo "▶ Installing kggenerator..."
helm upgrade --install kggenerator "$HELM_DIR/kggenerator" \
  --namespace "$NAMESPACE"
echo "✓ kggenerator installed (Job is suspended — trigger via asapbackend admin UI)."

echo ""
echo "▶ Installing asapbackend..."
BACKEND_SETS="--set secrets.inferenceApiKey=${INFERENCE_API_KEY}"
[[ -n "$INFERENCE_BASE_URL" ]] && BACKEND_SETS="$BACKEND_SETS --set env.inferenceBaseUrl=${INFERENCE_BASE_URL}"
[[ -n "$INFERENCE_MODEL"    ]] && BACKEND_SETS="$BACKEND_SETS --set env.inferenceModel=${INFERENCE_MODEL}"
[[ -n "$INFERENCE_REASONING_EFFORT" ]] && BACKEND_SETS="$BACKEND_SETS --set env.inferenceReasoningEffort=${INFERENCE_REASONING_EFFORT}"
# Numeric values are passed with --set-string so helm keeps them as strings
# (the configmap template quotes them; pydantic does the type conversion).
[[ -n "$INFERENCE_MIN_P"       ]] && BACKEND_SETS="$BACKEND_SETS --set-string env.inferenceMinP=${INFERENCE_MIN_P}"
[[ -n "$INFERENCE_TEMPERATURE" ]] && BACKEND_SETS="$BACKEND_SETS --set-string env.inferenceTemperature=${INFERENCE_TEMPERATURE}"
[[ -n "$INFERENCE_CONTEXT_WINDOW_TOKENS" ]] && BACKEND_SETS="$BACKEND_SETS --set-string env.inferenceContextWindowTokens=${INFERENCE_CONTEXT_WINDOW_TOKENS}"
[[ -n "$ASAP_ENV"           ]] && BACKEND_SETS="$BACKEND_SETS --set env.asapEnv=${ASAP_ENV}"
if [[ -n "$INGRESS_HOST" ]]; then
  # DEPRECATED 2026-07-24: the backend is intentionally not exposed via its
  # own Ingress — the browser reaches it only through the asapui nginx /api
  # reverse proxy (single front door). The flag is accepted but ignored so
  # existing command lines keep working. Rollback: see
  # docs/rollback/asapbackend-ingress.yaml or re-add ingress.enabled=true here.
  echo "NOTE: --ingress-host is deprecated and ignored; the backend is reachable via the UI /api proxy only."
fi
# shellcheck disable=SC2086
helm upgrade --install asapbackend "$HELM_DIR/asapbackend" \
  --namespace "$NAMESPACE" \
  $BACKEND_SETS
echo "✓ asapbackend installed."

echo ""
echo "▶ Installing asapui..."
UI_SETS=""
if [[ -n "$UI_HOST" ]]; then
  UI_SETS="--set ingress.enabled=true --set ingress.host=${UI_HOST}"
fi
# shellcheck disable=SC2086
helm upgrade --install asapui "$HELM_DIR/asapui" \
  --namespace "$NAMESPACE" \
  $UI_SETS
echo "✓ asapui installed."

echo ""
echo "All done. Check pod status with:"
echo "  kubectl get pods -n ${NAMESPACE}"

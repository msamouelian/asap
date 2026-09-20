#!/usr/bin/env bash
# Install all ASAP helm charts into a Kubernetes cluster.
# Compatible with both bash and zsh.
#
# ASAP uses AI models in three independently configurable places. Each group
# of flags below flows: script flag → helm --set → chart values.yaml →
# dedicated ConfigMap/Secret → pod environment variable read by the code.
#
#   Chat + RAG LLM      (--inference-*)     asapbackend: the conversational agent
#                                            AND the RAG query-distillation /
#                                            relevance-filter calls (one model).
#   Knowledge-graph LLM (--kg-inference-*)  kggenerator: entity extraction AND
#                                            entity-resolution adjudication.
#   Embedding model     (--embedding-*)     vllm serves it; neo4j (hybrid search
#                                            via the GenAI plugin), asapbackend,
#                                            asapextractor, asapdocworker and
#                                            kggenerator all consume it. Defaults
#                                            to the in-cluster vLLM.
#
# Usage:
#   ./scripts/install-charts.sh \
#     --neo4j-password          '<password>'  \
#     --pg-user                 '<user>'      \
#     --pg-password             '<password>'  \
#     --aspace-user             '<username>'  \
#     --aspace-password         '<password>'  \
#     --keycloak-admin-password '<password>'  \  # Keycloak master admin password
#     --inference-base-url      '<url>'       \  # chat+RAG LLM, OpenAI-compatible (e.g. http://host.k3d.internal:1234/v1, https://api.openai.com/v1)
#     --inference-model         '<model>'     \  # chat+RAG model name
#     --inference-api-key       '<key>'       \  # chat+RAG API key (LM Studio accepts any non-empty token)
#     --kg-inference-base-url   '<url>'       \  # knowledge-graph LLM endpoint
#     --kg-inference-model      '<model>'     \  # knowledge-graph model name
#     --kg-inference-api-key    '<key>'       \  # knowledge-graph API key
#     [--kg-inference-temperature '<t>']         \  # -1 omits the parameter (REQUIRED for OpenAI gpt-5-class); default 0
#     [--kg-inference-reasoning-effort '<e>']    \  # e.g. 'low' for OpenAI; omit for LM Studio/gpt-oss
#     [--inference-reasoning-effort '<effort>']  \  # 'none' REQUIRED for OpenAI gpt-5-class + tools on chat completions; omit for LM Studio/gpt-oss
#     [--inference-min-p '<0..1>']               \  # llama.cpp/LM Studio/vLLM extension; set 0 for api.openai.com (rejects it)
#     [--inference-temperature '<t>']            \  # -1 omits the parameter (required for OpenAI reasoning-class models)
#     [--inference-context-window-tokens '<n>']  \  # model context size, drives the UI usage percentage
#     [--embedding-base-url 'http://vllm-embedding:8000/v1'] \  # embedding endpoint (default: in-cluster vLLM)
#     [--embedding-model 'BAAI/bge-small-en-v1.5']           \  # embedding model; vllm serves it, every consumer names it
#     [--embedding-api-key '<key>']                          \  # bearer token for a HOSTED embedding endpoint; omit for in-cluster vLLM
#     [--embedding-dimensions 384]                           \  # vector size the model produces; every vector index is built with it
#     [--embedding-batch-size 64]                            \  # texts per embedding request in the extraction/ingestion jobs
#     [--asap-env development|production]    \
#     [--namespace asap]                     \
#     [--skip-extractor]                     \
#     [--ingress-host asapbackend.localhost] \  # DEPRECATED, ignored (backend has no direct ingress)
#     [--ui-host asapui.localhost]           \  # enable UI ingress (.localhost resolves natively)
#     [--neo4j-host neo4j.localhost]         \  # enable Neo4j browser ingress
#     [--keycloak-host keycloak.localhost]      # enable Keycloak ingress

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
NAMESPACE="asap"
CLUSTER_NAME="asap"
SKIP_EXTRACTOR=false

NEO4J_PASSWORD=""
PG_USER=""
PG_PASSWORD=""
ASPACE_USER=""
ASPACE_PASSWORD=""
KEYCLOAK_ADMIN_PASSWORD=""

# Chat + RAG LLM (asapbackend)
INFERENCE_BASE_URL=""
INFERENCE_MODEL=""
INFERENCE_API_KEY=""
INFERENCE_REASONING_EFFORT=""
INFERENCE_MIN_P=""
INFERENCE_TEMPERATURE=""
INFERENCE_CONTEXT_WINDOW_TOKENS=""

# Knowledge-graph LLM (kggenerator)
KG_INFERENCE_BASE_URL=""
KG_INFERENCE_MODEL=""
KG_INFERENCE_API_KEY=""
KG_INFERENCE_TEMPERATURE=""
KG_INFERENCE_REASONING_EFFORT=""

# Embedding model (vllm + every consumer). Defaults to the in-cluster server.
EMBEDDING_BASE_URL="http://vllm-embedding:8000/v1"
EMBEDDING_MODEL="BAAI/bge-small-en-v1.5"
EMBEDDING_API_KEY=""        # empty = in-cluster vLLM (no auth)
EMBEDDING_DIMENSIONS="384"  # bge-small-en-v1.5; text-embedding-3-small = 1536
EMBEDDING_BATCH_SIZE="64"

ASAP_ENV=""
INGRESS_HOST=""
UI_HOST=""
NEO4J_HOST=""
KEYCLOAK_HOST=""

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --neo4j-password)           NEO4J_PASSWORD="$2";          shift 2 ;;
    --pg-user)                  PG_USER="$2";                 shift 2 ;;
    --pg-password)              PG_PASSWORD="$2";             shift 2 ;;
    --aspace-user)              ASPACE_USER="$2";             shift 2 ;;
    --aspace-password)          ASPACE_PASSWORD="$2";         shift 2 ;;
    --keycloak-admin-password)  KEYCLOAK_ADMIN_PASSWORD="$2"; shift 2 ;;
    # Chat + RAG LLM
    --inference-base-url)              INFERENCE_BASE_URL="$2";              shift 2 ;;
    --inference-model)                 INFERENCE_MODEL="$2";                 shift 2 ;;
    --inference-api-key)               INFERENCE_API_KEY="$2";               shift 2 ;;
    --inference-reasoning-effort)      INFERENCE_REASONING_EFFORT="$2";      shift 2 ;;
    --inference-min-p)                 INFERENCE_MIN_P="$2";                 shift 2 ;;
    --inference-temperature)           INFERENCE_TEMPERATURE="$2";           shift 2 ;;
    --inference-context-window-tokens) INFERENCE_CONTEXT_WINDOW_TOKENS="$2"; shift 2 ;;
    # Knowledge-graph LLM
    --kg-inference-base-url)    KG_INFERENCE_BASE_URL="$2";   shift 2 ;;
    --kg-inference-model)       KG_INFERENCE_MODEL="$2";      shift 2 ;;
    --kg-inference-api-key)     KG_INFERENCE_API_KEY="$2";    shift 2 ;;
    --kg-inference-temperature)      KG_INFERENCE_TEMPERATURE="$2";      shift 2 ;;
    --kg-inference-reasoning-effort) KG_INFERENCE_REASONING_EFFORT="$2"; shift 2 ;;
    # Embedding model
    --embedding-base-url)       EMBEDDING_BASE_URL="$2";      shift 2 ;;
    --embedding-model)          EMBEDDING_MODEL="$2";         shift 2 ;;
    --embedding-api-key)        EMBEDDING_API_KEY="$2";       shift 2 ;;
    --embedding-dimensions)     EMBEDDING_DIMENSIONS="$2";    shift 2 ;;
    --embedding-batch-size)     EMBEDDING_BATCH_SIZE="$2";    shift 2 ;;
    # Deployment options
    --asap-env)                 ASAP_ENV="$2";                shift 2 ;;
    --ingress-host)             INGRESS_HOST="$2";            shift 2 ;;
    --ui-host)                  UI_HOST="$2";                 shift 2 ;;
    --neo4j-host)               NEO4J_HOST="$2";              shift 2 ;;
    --keycloak-host)            KEYCLOAK_HOST="$2";           shift 2 ;;
    --namespace)                NAMESPACE="$2";               shift 2 ;;
    --skip-extractor)           SKIP_EXTRACTOR=true;          shift   ;;
    *)
      echo "Error: unknown argument '$1'"
      echo "See the usage comment at the top of this script."
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Validation — every model coordinate is required; there are no defaults in
# the charts or the code, by design (a phantom default is worse than an error).
# ---------------------------------------------------------------------------
MISSING=()
[[ -z "$NEO4J_PASSWORD"          ]] && MISSING+=("--neo4j-password")
[[ -z "$PG_USER"                 ]] && MISSING+=("--pg-user")
[[ -z "$PG_PASSWORD"             ]] && MISSING+=("--pg-password")
[[ -z "$ASPACE_USER"             ]] && MISSING+=("--aspace-user")
[[ -z "$ASPACE_PASSWORD"         ]] && MISSING+=("--aspace-password")
[[ -z "$KEYCLOAK_ADMIN_PASSWORD" ]] && MISSING+=("--keycloak-admin-password")
[[ -z "$INFERENCE_BASE_URL"      ]] && MISSING+=("--inference-base-url  (chat + RAG LLM)")
[[ -z "$INFERENCE_MODEL"         ]] && MISSING+=("--inference-model     (chat + RAG LLM)")
[[ -z "$INFERENCE_API_KEY"       ]] && MISSING+=("--inference-api-key   (chat + RAG LLM)")
[[ -z "$KG_INFERENCE_BASE_URL"   ]] && MISSING+=("--kg-inference-base-url  (knowledge-graph LLM)")
[[ -z "$KG_INFERENCE_MODEL"      ]] && MISSING+=("--kg-inference-model     (knowledge-graph LLM)")
[[ -z "$KG_INFERENCE_API_KEY"    ]] && MISSING+=("--kg-inference-api-key   (knowledge-graph LLM)")

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

# Embedding coordinates are passed to every chart that consumes them. Job
# charts (extractor, docworker, kggenerator) use the embedding.* block and a
# secrets.embeddingApiKey; asapbackend uses its env.* naming (set below).
EMBEDDING_SETS="--set embedding.baseUrl=${EMBEDDING_BASE_URL} --set embedding.model=${EMBEDDING_MODEL}"
EMBEDDING_SETS="$EMBEDDING_SETS --set-string embedding.dimensions=${EMBEDDING_DIMENSIONS} --set-string embedding.batchSize=${EMBEDDING_BATCH_SIZE}"
EMBEDDING_SETS="$EMBEDDING_SETS --set secrets.embeddingApiKey=${EMBEDDING_API_KEY}"

# ---------------------------------------------------------------------------
# Pre-flight: persisted data volumes survive a reinstall (Retain policy), but
# database passwords are only INITIALISED from these flags on an empty volume.
#   - PostgreSQL roles (asap, keycloak) are re-synced to the new Secret
#     automatically on every start by a lifecycle hook in their charts.
#   - Neo4j's password and Keycloak's ADMIN CONSOLE password are NOT synced:
#     see docs/runbook-passwords.md before changing either flag on an
#     existing cluster.
# ---------------------------------------------------------------------------
EXISTING_PVS=()
for pv in postgres-pv keycloak-postgres-pv neo4j-pv; do
  kubectl get pv "$pv" > /dev/null 2>&1 && EXISTING_PVS+=("$pv")
done
if [[ ${#EXISTING_PVS[@]} -gt 0 ]]; then
  echo ""
  echo "NOTE: existing data volumes detected (${EXISTING_PVS[*]}); data will be kept."
  echo "      If --neo4j-password or --keycloak-admin-password differ from the previous"
  echo "      install, follow docs/runbook-passwords.md — those two are set only when a"
  echo "      volume is first initialised and are not changed by this script."
fi

# ---------------------------------------------------------------------------
# Uninstall existing releases (clean reinstall)
# Uninstalled in reverse dependency order; silently skipped if not present.
# ---------------------------------------------------------------------------
echo ""
echo "Uninstalling existing ASAP helm releases from namespace '${NAMESPACE}'..."

# Jobs triggered at runtime by asapbackend (extractor runs, document ingestion,
# KG generation) are API-created, not helm-owned — helm uninstall never sees
# them. Remove them here so redeploys start clean; the suspended template jobs
# are chart-owned and get recreated by the installs below.
echo "  ▶ Deleting runtime-triggered jobs (extractor + docworker + kggenerator runs)..."
kubectl delete jobs --namespace "$NAMESPACE" -l triggered-by=asapbackend --ignore-not-found 2>/dev/null || true

for release in asapui asapbackend kggenerator asapdocworker neo4j-mcp vllm postgres neo4j keycloak; do
  if helm status "$release" --namespace "$NAMESPACE" > /dev/null 2>&1; then
    echo "  ▶ Uninstalling ${release}..."
    helm uninstall "$release" --namespace "$NAMESPACE" --wait
    echo "  ✓ ${release} uninstalled."
  else
    echo "  — ${release} not installed, skipping."
  fi
done

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
# consistent name (e.g. an LM Studio server on the Mac).
# ---------------------------------------------------------------------------
echo "▶ Registering host.k3d.internal in CoreDNS..."
DOCKER_HOST_IP=$(docker run --rm alpine getent ahostsv4 host.docker.internal \
  2>/dev/null | awk '{print $1}' | head -1)
if [[ -z "$DOCKER_HOST_IP" ]]; then
  echo "  Warning: could not resolve host.docker.internal — skipping CoreDNS patch."
  echo "  If a model server runs on this Mac, pass its IP explicitly in --inference-base-url / --kg-inference-base-url."
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
# The GenAI plugin embeds hybrid-search queries against the embedding endpoint.
NEO4J_SETS="--set genai.openaiBaseUrl=${EMBEDDING_BASE_URL}"
[[ -n "$NEO4J_HOST" ]] && NEO4J_SETS="$NEO4J_SETS --set ingress.enabled=true --set ingress.host=${NEO4J_HOST}"
# shellcheck disable=SC2086
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
echo "▶ Installing vllm (embedding server: ${EMBEDDING_MODEL})..."
helm upgrade --install vllm "$HELM_DIR/vllm" \
  --namespace "$NAMESPACE" \
  --set model.name="$EMBEDDING_MODEL"
echo "✓ vllm installed."
echo "  Note: the pod will not become ready until the model finishes downloading (~2–3 min on first boot)."

echo ""
echo "▶ Installing asapdocworker (document ingestion worker)..."
# shellcheck disable=SC2086
helm upgrade --install asapdocworker "$HELM_DIR/asapdocworker" \
  --namespace "$NAMESPACE" \
  $EMBEDDING_SETS
echo "✓ asapdocworker installed (Job is suspended — cloned per upload by asapbackend)."

if [[ "$SKIP_EXTRACTOR" == false ]]; then
  echo ""
  echo "▶ Installing asapextractor..."
  # shellcheck disable=SC2086
  helm upgrade --install asapextractor "$HELM_DIR/asapextractor" \
    --namespace "$NAMESPACE" \
    --set aspace.username="$ASPACE_USER" \
    --set aspace.password="$ASPACE_PASSWORD" \
    $EMBEDDING_SETS
  echo "✓ asapextractor installed (Job is suspended — trigger via asapbackend)."
else
  echo ""
  echo "  Skipping asapextractor (--skip-extractor set)."
fi

echo ""
echo "▶ Installing kggenerator (knowledge-graph LLM: ${KG_INFERENCE_MODEL} @ ${KG_INFERENCE_BASE_URL})..."
# shellcheck disable=SC2086
KG_SETS=""
[[ -n "$KG_INFERENCE_TEMPERATURE"      ]] && KG_SETS="$KG_SETS --set-string inference.temperature=${KG_INFERENCE_TEMPERATURE}"
[[ -n "$KG_INFERENCE_REASONING_EFFORT" ]] && KG_SETS="$KG_SETS --set inference.reasoningEffort=${KG_INFERENCE_REASONING_EFFORT}"
# shellcheck disable=SC2086
helm upgrade --install kggenerator "$HELM_DIR/kggenerator" \
  --namespace "$NAMESPACE" \
  --set inference.baseUrl="$KG_INFERENCE_BASE_URL" \
  --set inference.model="$KG_INFERENCE_MODEL" \
  --set secrets.inferenceApiKey="$KG_INFERENCE_API_KEY" \
  $KG_SETS $EMBEDDING_SETS
echo "✓ kggenerator installed (Job is suspended — trigger via asapbackend admin UI)."

echo ""
echo "▶ Installing asapbackend (chat + RAG LLM: ${INFERENCE_MODEL} @ ${INFERENCE_BASE_URL})..."
BACKEND_SETS="--set secrets.inferenceApiKey=${INFERENCE_API_KEY}"
BACKEND_SETS="$BACKEND_SETS --set env.inferenceBaseUrl=${INFERENCE_BASE_URL}"
BACKEND_SETS="$BACKEND_SETS --set env.inferenceModel=${INFERENCE_MODEL}"
BACKEND_SETS="$BACKEND_SETS --set env.embeddingBaseUrl=${EMBEDDING_BASE_URL}"
BACKEND_SETS="$BACKEND_SETS --set env.embeddingModel=${EMBEDDING_MODEL}"
BACKEND_SETS="$BACKEND_SETS --set secrets.embeddingApiKey=${EMBEDDING_API_KEY}"
[[ -n "$INFERENCE_REASONING_EFFORT" ]] && BACKEND_SETS="$BACKEND_SETS --set env.inferenceReasoningEffort=${INFERENCE_REASONING_EFFORT}"
# Numeric values are passed with --set-string so helm keeps them as strings
# (the configmap template quotes them; pydantic does the type conversion).
[[ -n "$INFERENCE_MIN_P"       ]] && BACKEND_SETS="$BACKEND_SETS --set-string env.inferenceMinP=${INFERENCE_MIN_P}"
[[ -n "$INFERENCE_TEMPERATURE" ]] && BACKEND_SETS="$BACKEND_SETS --set-string env.inferenceTemperature=${INFERENCE_TEMPERATURE}"
[[ -n "$INFERENCE_CONTEXT_WINDOW_TOKENS" ]] && BACKEND_SETS="$BACKEND_SETS --set-string env.inferenceContextWindowTokens=${INFERENCE_CONTEXT_WINDOW_TOKENS}"
[[ -n "$ASAP_ENV"              ]] && BACKEND_SETS="$BACKEND_SETS --set env.asapEnv=${ASAP_ENV}"
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
echo "All done. Model configuration in effect:"
echo "  Chat + RAG LLM:       ${INFERENCE_MODEL} @ ${INFERENCE_BASE_URL}"
echo "  Knowledge-graph LLM:  ${KG_INFERENCE_MODEL} @ ${KG_INFERENCE_BASE_URL}"
echo "  Embedding model:      ${EMBEDDING_MODEL} @ ${EMBEDDING_BASE_URL} (${EMBEDDING_DIMENSIONS} dims, batch ${EMBEDDING_BATCH_SIZE}$( [[ -n "$EMBEDDING_API_KEY" ]] && echo ', authenticated' ))"
echo "Check pod status with:"
echo "  kubectl get pods -n ${NAMESPACE}"

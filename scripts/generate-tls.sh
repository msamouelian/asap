#!/usr/bin/env bash
# Generate locally-trusted TLS certificates for ASAP using OpenSSL.
#
# Creates a local CA and a single wildcard-SAN certificate covering all
# ASAP domains. Output files are written to CERT_DIR.
#
# Run once (or with --force to regenerate). After generating, run
# setup-tls.sh to install the CA into the OS trust store and load
# the certificate into Kubernetes.
#
# Prerequisites:
#   openssl (ships with macOS)
#
# Usage:
#   ./scripts/generate-tls.sh [--cert-dir /path/to/certs] [--force]

set -euo pipefail

CERT_DIR="/Users/msamouelian/Documents/asapcert"
FORCE=false
DOMAINS=(asapui.localhost keycloak.localhost asapbackend.localhost neo4j.localhost)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cert-dir) CERT_DIR="$2"; shift 2 ;;
    --force)    FORCE=true; shift ;;
    *) echo "Error: unknown argument '$1'"; exit 1 ;;
  esac
done

CA_KEY="${CERT_DIR}/asap-ca.key"
CA_CERT="${CERT_DIR}/asap-ca.crt"
TLS_KEY="${CERT_DIR}/asap-tls.key"
TLS_CERT="${CERT_DIR}/asap-tls.crt"
TLS_CSR="${CERT_DIR}/asap-tls.csr"
SAN_CNF="${CERT_DIR}/asap-san.cnf"

# ---------------------------------------------------------------------------
# Guard: skip if certs already exist (unless --force)
# ---------------------------------------------------------------------------
if [[ -f "$TLS_CERT" && -f "$TLS_KEY" && "$FORCE" == "false" ]]; then
  echo "Certificates already exist at ${CERT_DIR}."
  echo "  Pass --force to regenerate."
  exit 0
fi

mkdir -p "$CERT_DIR"

# ---------------------------------------------------------------------------
# 1. Local CA — reused if present so the copy already installed in the OS
#    trust store stays valid; only the leaf certificate is regenerated (e.g.
#    after a SAN/domain change). Delete the CA files to force a new CA.
# ---------------------------------------------------------------------------
if [[ -f "$CA_KEY" && -f "$CA_CERT" ]]; then
  echo "▶ Reusing existing local CA (trust store stays valid)."
else
  echo "▶ Generating local CA..."
  openssl genrsa -out "$CA_KEY" 4096 2>/dev/null
  openssl req -x509 -new -nodes \
    -key "$CA_KEY" -sha256 -days 3650 \
    -out "$CA_CERT" \
    -subj "/O=ASAP Local CA/CN=ASAP Local CA"
fi
echo "✓ CA key:  ${CA_KEY}"
echo "✓ CA cert: ${CA_CERT}"

# ---------------------------------------------------------------------------
# 2. SAN config
# ---------------------------------------------------------------------------
{
  echo "[req]"
  echo "req_extensions     = v3_req"
  echo "distinguished_name = req_distinguished_name"
  echo "[req_distinguished_name]"
  echo "[v3_req]"
  echo "subjectAltName  = @alt_names"
  echo "basicConstraints = CA:FALSE"
  echo "keyUsage         = digitalSignature, keyEncipherment"
  echo "[alt_names]"
  for i in "${!DOMAINS[@]}"; do
    echo "DNS.$((i+1)) = ${DOMAINS[$i]}"
  done
} > "$SAN_CNF"

# ---------------------------------------------------------------------------
# 3. Domain key + CSR
# ---------------------------------------------------------------------------
echo ""
echo "▶ Generating domain key and CSR..."
openssl genrsa -out "$TLS_KEY" 2048 2>/dev/null
openssl req -new \
  -key "$TLS_KEY" \
  -out "$TLS_CSR" \
  -subj "/CN=${DOMAINS[0]}" \
  -config "$SAN_CNF"

# ---------------------------------------------------------------------------
# 4. Sign with CA
# ---------------------------------------------------------------------------
echo "▶ Signing certificate (valid 825 days)..."
openssl x509 -req \
  -in "$TLS_CSR" \
  -CA "$CA_CERT" -CAkey "$CA_KEY" -CAcreateserial \
  -out "$TLS_CERT" \
  -days 825 -sha256 \
  -extensions v3_req -extfile "$SAN_CNF" 2>/dev/null
echo "✓ Certificate: ${TLS_CERT}"

# ---------------------------------------------------------------------------
# 5. Cleanup temp files
# ---------------------------------------------------------------------------
rm -f "$TLS_CSR" "$SAN_CNF" "${CERT_DIR}/asap-ca.srl"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "Domains covered:"
for d in "${DOMAINS[@]}"; do echo "  • $d"; done
echo ""
echo "Next step — install into trust store and Kubernetes:"
echo "  ./scripts/setup-tls.sh --cert-dir ${CERT_DIR}"

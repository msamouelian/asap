#!/usr/bin/env bash
# Install ASAP TLS certificates into Kubernetes (and optionally the OS trust store).
#
# Expects pre-generated OpenSSL certificates at CERT_DIR (default:
# /Users/msamouelian/Documents/asapcert). Re-run after each cluster recreation
# to restore the Kubernetes secret (the cert files on disk are preserved).
#
# The macOS Keychain step (--install-ca) only needs to be done once per machine,
# or whenever the CA is regenerated. It requires sudo and is skipped by default
# so that automated reinstalls (e.g. from install-charts.sh) don't prompt for a
# password.
#
# Trust store notes:
#   macOS/Chrome/Safari: run once with --install-ca to add the CA to the System
#                        Keychain. Restart the browser afterwards.
#   Firefox:             Import asap-ca.crt manually via:
#                          Settings → Privacy & Security → Certificates
#                          → View Certificates → Authorities → Import
#                        OR set about:config security.enterprise_roots.enabled=true
#                        to use the OS trust store (same as Chrome).
#
# Usage:
#   ./scripts/setup-tls.sh [--cert-dir /path/to/certs] [--namespace asap] [--install-ca]

set -euo pipefail

CERT_DIR="/Users/msamouelian/Documents/asapcert"
NAMESPACE="asap"
INSTALL_CA=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cert-dir)   CERT_DIR="$2";   shift 2 ;;
    --namespace)  NAMESPACE="$2";  shift 2 ;;
    --install-ca) INSTALL_CA=true; shift   ;;
    *) echo "Error: unknown argument '$1'"; exit 1 ;;
  esac
done

CA_CERT="${CERT_DIR}/asap-ca.crt"
TLS_CERT="${CERT_DIR}/asap-tls.crt"
TLS_KEY="${CERT_DIR}/asap-tls.key"
SECRET_NAME="asap-tls"

# ---------------------------------------------------------------------------
# Validate inputs
# ---------------------------------------------------------------------------
for f in "$CA_CERT" "$TLS_CERT" "$TLS_KEY"; do
  if [[ ! -f "$f" ]]; then
    echo "Error: expected file not found: $f"
    echo "  Regenerate with: ./scripts/setup-tls.sh --cert-dir <dir>"
    exit 1
  fi
done

# ---------------------------------------------------------------------------
# Install CA into macOS System Keychain (Chrome / Safari) — optional
# ---------------------------------------------------------------------------
if [[ "$INSTALL_CA" == true ]]; then
  echo "▶ Installing CA into macOS System Keychain (Chrome/Safari)..."
  sudo security add-trusted-cert -d -r trustRoot \
    -k /Library/Keychains/System.keychain "$CA_CERT"
  echo "✓ CA trusted. Restart Chrome/Safari if certs aren't recognised immediately."
  echo ""
  echo "  Firefox users: import ${CA_CERT} via:"
  echo "    Settings → Privacy & Security → Certificates → View Certificates"
  echo "    → Authorities → Import → check 'Trust this CA to identify websites'"
  echo "  OR set about:config  security.enterprise_roots.enabled = true"
else
  echo "  Skipping CA trust store installation (pass --install-ca to enable)."
fi

# ---------------------------------------------------------------------------
# Install TLS secret into Kubernetes
# ---------------------------------------------------------------------------
echo ""
echo "▶ Installing TLS secret '${SECRET_NAME}' in namespace '${NAMESPACE}'..."
kubectl get namespace "$NAMESPACE" > /dev/null 2>&1 \
  || kubectl create namespace "$NAMESPACE"

kubectl create secret tls "$SECRET_NAME" \
  --cert="$TLS_CERT" \
  --key="$TLS_KEY" \
  --namespace "$NAMESPACE" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "✓ TLS secret '${SECRET_NAME}' installed."

echo ""
echo "All ASAP ingresses now use HTTPS."

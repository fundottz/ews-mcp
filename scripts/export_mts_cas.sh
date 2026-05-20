#!/usr/bin/env bash
# Re-export MTS corporate roots from macOS System keychain into mts-extra.pem.
# Run once after VPN/profile changes if SSL verify fails again.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXTRA="${SCRIPT_DIR}/../certs/mts-extra.pem"
KEYCHAIN="/Library/Keychains/System.keychain"

NAMES=(
  "MTS Root CA"
  "MTS Class 2 Root CA G2"
  "MTS WinCA G3"
  "PJSCMtsbOfflineRootPKI"
  "PJSCMtsbIntermediateEnterprisePKI"
)

: > "$EXTRA"
for name in "${NAMES[@]}"; do
  security find-certificate -a -p -c "$name" "$KEYCHAIN" 2>/dev/null >> "$EXTRA" || true
done

count="$(grep -c 'BEGIN CERTIFICATE' "$EXTRA" || true)"
if [[ "${count}" -eq 0 ]]; then
  echo "export_mts_cas.sh: no certificates exported from ${KEYCHAIN}" >&2
  echo "Check VPN/keychain or add CA names to NAMES[] in this script." >&2
  exit 1
fi
echo "Wrote ${count} certificate(s) to ${EXTRA}"
rm -f "${SCRIPT_DIR}/../certs/combined-ca-bundle.pem"

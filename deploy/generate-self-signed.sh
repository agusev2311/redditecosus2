#!/usr/bin/env bash
set -euo pipefail

DOMAIN_OR_IP="${1:-localhost}"
OUT_DIR="${2:-./deploy/certs}"
DAYS="${3:-825}"

mkdir -p "$OUT_DIR"

CRT_PATH="$OUT_DIR/server.crt"
KEY_PATH="$OUT_DIR/server.key"
CFG_PATH="$OUT_DIR/openssl.cnf"

if [[ "$DOMAIN_OR_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  SAN_LINE="IP.1 = $DOMAIN_OR_IP"
else
  SAN_LINE="DNS.1 = $DOMAIN_OR_IP"
fi

cat > "$CFG_PATH" <<EOF
[req]
default_bits = 4096
prompt = no
default_md = sha256
x509_extensions = v3_req
distinguished_name = dn

[dn]
CN = $DOMAIN_OR_IP

[v3_req]
subjectAltName = @alt_names
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[alt_names]
$SAN_LINE
EOF

openssl req \
  -x509 \
  -nodes \
  -newkey rsa:4096 \
  -keyout "$KEY_PATH" \
  -out "$CRT_PATH" \
  -days "$DAYS" \
  -config "$CFG_PATH"

echo "Generated:"
echo "  cert: $CRT_PATH"
echo "  key:  $KEY_PATH"
echo
echo "Use these in .env:"
echo "APP_DOMAIN=$DOMAIN_OR_IP"
echo "TLS_CERT_PATH=$CRT_PATH"
echo "TLS_KEY_PATH=$KEY_PATH"
echo "EXTRA_CA_CERTS_PATH=$OUT_DIR"


# Docker Deployment

## Services

- `backend`: Flask API only. It serves requests and does not run heavy media processing.
- `processor`: dedicated background process for queue consumption and AI analysis.
- `frontend`: Nginx serving the built SPA and proxying `/api/*` to `backend`.
- `telegram-bot`: separate long-running bot process sharing the same app data.

## Automated setup

For a fresh Linux host, prefer the helper script:

```bash
chmod +x ./deploy/setup.sh
./deploy/setup.sh \
  --domain 95.62.49.206 \
  --ai-proxy-base-url https://95.62.49.206:8317/v1 \
  --ai-proxy-api-key sk-... \
  --generate-self-signed \
  --up
```

It writes the root `.env`, writes `backend/.env`, generates `APP_SECRET_KEY`, optionally creates self-signed TLS certs, and can run `docker compose up -d --build`.

## Persistent state

All mutable state is redirected into `APP_DATA_ROOT`:

- `library.db`
- `storage/media`
- `storage/archives`
- `storage/backups`
- `storage/logs`

In Docker this path is mounted as `/app/data` through the named volume `app_data`.

## TLS model

- TLS is terminated at Nginx in the `frontend` container.
- HTTP on port 80 always redirects to HTTPS.
- Reverse proxy headers are trusted in Flask through `ProxyFix`.
- Browser-facing requests in production use same-origin `/api`, so there is no mixed-content issue.

## Self-signed certificate paths

Compose mounts:

- `${TLS_CERT_PATH}` -> `/etc/nginx/certs/server.crt`
- `${TLS_KEY_PATH}` -> `/etc/nginx/certs/server.key`
- `${EXTRA_CA_CERTS_PATH}` -> `/run/certs`

If certificates already live elsewhere on the server, just point these variables to the existing files.

## AI proxy over self-signed TLS

If the external proxy itself uses a self-signed certificate:

1. Preferred:
   - place its CA bundle under `${EXTRA_CA_CERTS_PATH}`
   - set `AI_PROXY_CA_BUNDLE=/run/certs/<your-ca>.pem` in `backend/.env`
2. Fallback:
   - set `AI_PROXY_VERIFY_TLS=false`

The second option is simpler but weaker from a security standpoint.

# Docker Deployment

## Services

- `backend`: Flask API + internal processing workers, one Gunicorn worker to avoid duplicate queue consumers.
- `frontend`: Nginx serving the built SPA and proxying `/api/*` to `backend`.
- `telegram-bot`: separate long-running bot process sharing the same app data.

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

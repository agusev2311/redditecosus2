# TLS Helper

## Full setup

Для полноценной первичной настройки сервера используйте:

```bash
chmod +x ./deploy/setup.sh
./deploy/setup.sh
```

Без аргументов скрипт переходит в интерактивный режим и по очереди спрашивает все нужные значения.

Можно и сразу одной командой:

```bash
chmod +x ./deploy/setup.sh
./deploy/setup.sh \
  --domain 95.62.49.206 \
  --ai-proxy-base-url https://95.62.49.206:8317/v1 \
  --ai-proxy-api-key sk-... \
  --generate-self-signed \
  --up
```

Скрипт настроит `.env`, `backend/.env`, сертификаты и при `--up` сразу поднимет стек.

Если конфиги уже существуют, добавьте `--force`: перед изменением будут созданы backup-копии.

## TLS only

Generate a self-signed certificate with SAN support:

```bash
chmod +x ./deploy/generate-self-signed.sh
./deploy/generate-self-signed.sh 95.62.49.206
```

Or with a domain:

```bash
./deploy/generate-self-signed.sh example.com
```

Then copy the printed values into the root `.env`.

Notes:

- Modern clients require `subjectAltName`, not just `CN`.
- For a public site, a trusted certificate is still better than self-signed.
- Telegram inline media may work unreliably with self-signed HTTPS.

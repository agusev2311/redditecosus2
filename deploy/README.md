# TLS Helper

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


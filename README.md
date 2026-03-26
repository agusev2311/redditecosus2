# Reddit Ecosystem 2

Моно-репозиторий для приватной мультимедийной библиотеки с:

- Flask API и многопользовательской изоляцией данных.
- Node.js/Vite фронтендом с адаптивным стеклянным интерфейсом.
- AI-индексацией изображений, GIF и видео через OpenAI-compatible proxy API.
- Загрузкой архивов с вложенными папками.
- Аналитикой диска, очередью обработки, аудитом, резервными копиями и Telegram-ботом.

## Структура

- `backend/` - Flask API, очередь индексации, Telegram-бот, резервные копии.
- `frontend/` - SPA для поиска, загрузки, админки и аналитики.
- `docs/` - заметки по архитектуре и интеграциям.

## Быстрый старт

### Backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python run.py
```

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

## Docker + TLS

### Что подготовить

1. Скопируйте `backend/.env.example` в `backend/.env` и заполните как минимум:
   - `APP_SECRET_KEY`
   - `AI_PROXY_API_KEY`
   - `TELEGRAM_BOT_TOKEN` и `TELEGRAM_BACKUP_CHAT_ID`, если нужен бот
2. Скопируйте `.env.docker.example` в корневой `.env`.
3. Укажите домен или IP в `APP_DOMAIN`.
4. Укажите пути к self-signed сертификатам:
   - `TLS_CERT_PATH`
   - `TLS_KEY_PATH`
5. Если хотите пробросить внутрь контейнеров свой каталог CA/сертификатов, укажите `EXTRA_CA_CERTS_PATH`.

По умолчанию compose ожидает их в `deploy/certs/server.crt` и `deploy/certs/server.key`, но можно указать и абсолютные пути на сервере.

### Запуск

```bash
docker compose up -d --build
```

После запуска:

- фронтенд и API доступны через `https://<APP_DOMAIN>`
- HTTP на `:80` редиректится на HTTPS
- Nginx принимает большие загрузки без лимита размера
- backend и telegram-bot используют общий persistent volume `app_data`

### Важно про self-signed

- Браузер будет ругаться на самоподписанный сертификат, пока вы явно не доверите ему этот CA/сертификат на клиентской машине.
- Если ваш `AI_PROXY_BASE_URL` тоже работает через self-signed TLS, есть 2 варианта:
  - безопасный: положить CA bundle в каталог `EXTRA_CA_CERTS_PATH` и указать `AI_PROXY_CA_BUNDLE=/run/certs/<ваш-ca>.pem`
  - быстрый: поставить `AI_PROXY_VERIFY_TLS=false`

### Что внутри docker-стека

- `backend` - Flask API под `gunicorn`
- `frontend` - React build + `nginx` reverse proxy с TLS
- `telegram-bot` - отдельный процесс для Telegram polling и inline mode

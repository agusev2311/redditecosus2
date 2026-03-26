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


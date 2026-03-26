FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production \
    APP_DATA_ROOT=/app/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libglib2.0-0 \
        libmagic1 \
        libsm6 \
        libxext6 \
        libxrender1 \
        unrar-free \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . .

RUN mkdir -p /app/data

EXPOSE 5000

CMD ["gunicorn", "--worker-class", "gthread", "--workers", "1", "--threads", "8", "--timeout", "300", "--bind", "0.0.0.0:5000", "run:app"]


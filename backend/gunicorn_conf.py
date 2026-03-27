from __future__ import annotations

import os


bind = os.getenv("GUNICORN_BIND", "0.0.0.0:5000")
worker_class = "gthread"
workers = int(os.getenv("GUNICORN_WORKERS", "1"))
threads = int(os.getenv("GUNICORN_THREADS", "2"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "300"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")
access_log_format = (
    '%(h)s %(l)s %(u)s [%(t)s] "%(r)s" %(s)s %(b)s '
    '"%(f)s" "%(a)s" %(D)sus'
)

import multiprocessing
import os


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 10_000) -> int:
    raw = os.getenv(name)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw.strip())
        except ValueError:
            value = default
    value = max(minimum, value)
    value = min(maximum, value)
    return value


cpu_count = max(1, multiprocessing.cpu_count())
default_workers = min(8, max(2, cpu_count * 2))

# Binding
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

# Worker model
worker_class = "gthread"
workers = _env_int("WEB_CONCURRENCY", default_workers, minimum=1, maximum=32)
threads = _env_int("GUNICORN_THREADS", 4, minimum=1, maximum=32)

# Timeouts and connection behavior
timeout = _env_int("GUNICORN_TIMEOUT", 120, minimum=30, maximum=600)
graceful_timeout = _env_int("GUNICORN_GRACEFUL_TIMEOUT", 30, minimum=10, maximum=300)
keepalive = _env_int("GUNICORN_KEEPALIVE", 5, minimum=1, maximum=120)

# Recycle workers to reduce risk of gradual memory growth
max_requests = _env_int("GUNICORN_MAX_REQUESTS", 1000, minimum=0, maximum=100000)
max_requests_jitter = _env_int("GUNICORN_MAX_REQUESTS_JITTER", 100, minimum=0, maximum=100000)

if os.path.isdir("/dev/shm"):
    worker_tmp_dir = "/dev/shm"

# Logging
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from typing import Dict

from dotenv import load_dotenv
from flask import Flask, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix

from db_utils import init_schema

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _is_running_on_render() -> bool:
    return _env_bool("RENDER", default=False) or bool(os.getenv("RENDER_SERVICE_ID"))


def _configure_logging() -> None:
    if getattr(_configure_logging, "_configured", False):
        return

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    # Avoid duplicate handlers under WSGI reloads.
    logger.handlers = []

    # Always add console handler for cloud deployments (Render captures stdout/stderr)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    enable_file_logging = _env_bool(
        "ENABLE_FILE_LOGGING", default=not _is_running_on_render()
    )
    if enable_file_logging:
        # Optional file logging - only if directory exists or can be created
        log_file = os.getenv("LOG_FILE", os.path.join(BASE_DIR, "app.log"))
        try:
            log_dir = os.path.dirname(log_file)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)

            max_bytes = int(os.getenv("LOG_MAX_BYTES", "5000000"))
            backup_count = int(os.getenv("LOG_BACKUP_COUNT", "3"))

            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
            logger.info("File logging enabled: %s", log_file)
        except (OSError, PermissionError) as e:
            # Gracefully fall back to console-only logging
            logger.warning("File logging disabled (could not create %s): %s", log_file, e)
    else:
        logger.info("File logging disabled (ENABLE_FILE_LOGGING=0).")

    _configure_logging._configured = True


def _load_env() -> None:
    dotenv_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path, override=False)


def _load_users_from_env() -> Dict[str, str]:
    users: Dict[str, str] = {}
    for key, value in os.environ.items():
        if not re.fullmatch(r"USER\d+", key):
            continue
        try:
            uname, hashed_pwd = value.split(":", 1)
            uname = uname.strip()
            hashed_pwd = hashed_pwd.strip()
            if not uname or not hashed_pwd:
                raise ValueError("username/hash empty")
            users[uname] = hashed_pwd
        except ValueError:
            logging.getLogger(__name__).warning(
                "Invalid user format in %s (expected USERX=username:bcrypt_hash)", key
            )
    return users


def create_app() -> Flask:
    _load_env()
    _configure_logging()

    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    app.secret_key = os.getenv("SECRET_KEY")
    if not app.secret_key:
        raise ValueError("SECRET_KEY environment variable is not set!")

    app.config.update(
        UPLOAD_FOLDER=os.getenv("UPLOAD_FOLDER", os.path.join(BASE_DIR, "uploads")),
        REPORT_FOLDER=os.getenv("REPORT_FOLDER", os.path.join(BASE_DIR, "reports")),
        MAX_CONTENT_LENGTH=int(os.getenv("MAX_CONTENT_LENGTH", str(16 * 1024 * 1024))),
        ALLOWED_EXTENSIONS={"xls", "xlsx"},
        DATABASE_FILE=os.getenv("DATABASE_FILE", os.path.join(BASE_DIR, "order_counter.db")),
    )

    # Safe startup log for storage config (bucket + key role, no secrets).
    from storage_utils import log_storage_startup

    log_storage_startup()

    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app_env = (os.getenv("APP_ENV") or "development").strip().lower()
    if app_env in {"production", "prod"}:
        require_external_db = _env_bool(
            "REQUIRE_EXTERNAL_DB", default=_is_running_on_render()
        )
        has_external_db = bool(
            (os.getenv("DATABASE_URL") or "").strip()
            or (os.getenv("SUPABASE_DATABASE_URL") or "").strip()
        )
        if require_external_db and not has_external_db:
            raise ValueError(
                "DATABASE_URL (or SUPABASE_DATABASE_URL) is required in production when REQUIRE_EXTERNAL_DB=1."
            )

    default_secure_cookie = app_env in {"production", "prod"} or _is_running_on_render()
    app.config["SESSION_COOKIE_SECURE"] = _env_bool(
        "SESSION_COOKIE_SECURE", default=default_secure_cookie
    )
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    # Used by API auth (/api/v1/auth/login)
    app.config["USERS_DICT"] = _load_users_from_env()

    # Only create local directories (skip for cloud storage URLs like Supabase S3)
    upload_folder = app.config["UPLOAD_FOLDER"]
    report_folder = app.config["REPORT_FOLDER"]

    if not upload_folder.startswith(("http://", "https://")):
        os.makedirs(upload_folder, exist_ok=True)
    if not report_folder.startswith(("http://", "https://")):
        os.makedirs(report_folder, exist_ok=True)

    # Ensure DB schema exists — non-fatal so the app can still start
    # even if the database is temporarily unreachable (e.g. Supabase paused).
    try:
        init_schema(default_sqlite_db_file=app.config["DATABASE_FILE"])
    except Exception as e:
        logging.getLogger(__name__).error(
            "init_schema failed (app will start, but DB may be unavailable): %s", e
        )

    # Register API routes only (frontend is deployed separately on GitHub Pages).
    from api import api_bp

    app.register_blueprint(api_bp)

    @app.get("/")
    def index():
        return jsonify(
            {
                "status": "ok",
                "service": "sale-order-api",
                "health": "/api/v1/health",
                "ready": "/api/v1/ready",
            }
        )

    return app


app = create_app()


if __name__ == "__main__":
    debug = _env_bool("FLASK_DEBUG", default=False)
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    app.run(debug=debug, host=host, port=port)

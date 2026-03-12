import logging
import os
import time
from functools import lru_cache
from typing import Any, Dict, Iterable, Optional, Tuple, Union

from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)


def _env_int(
    name: str,
    default: int,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    raw = os.getenv(name)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw.strip())
        except ValueError:
            value = default

    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _sqlite_url(db_file: str) -> str:
    # SQLAlchemy expects forward slashes for sqlite file URLs.
    db_file_abs = os.path.abspath(db_file)
    return "sqlite:///" + db_file_abs.replace("\\", "/")


def get_database_url(*, default_sqlite_db_file: str) -> str:
    url = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DATABASE_URL")
    if url:
        url = url.strip()
        # Heroku-style URLs sometimes come as postgres://
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]

        # Supabase transaction pooler (:6543) can timeout in some networks.
        # For persistent Flask services, session mode (:5432) is typically more stable.
        if "supabase.com" in url and ":6543" in url:
            url = url.replace(":6543", ":5432")

        return url

    return _sqlite_url(default_sqlite_db_file)


def is_postgres_url(url: str) -> bool:
    u = (url or "").lower()
    return u.startswith("postgresql://") or u.startswith("postgres://")


@lru_cache(maxsize=8)
def get_engine(database_url: str):
    connect_args: Dict[str, Any] = {}
    postgres = is_postgres_url(database_url)

    if postgres:
        # psycopg2 settings
        connect_args["sslmode"] = os.getenv("DB_SSLMODE", "require").strip().lower()
        connect_args["connect_timeout"] = _env_int(
            "DB_CONNECT_TIMEOUT", 30, minimum=5, maximum=120
        )
        connect_args["keepalives"] = 1
        connect_args["keepalives_idle"] = _env_int(
            "DB_KEEPALIVES_IDLE", 30, minimum=5, maximum=600
        )
        connect_args["keepalives_interval"] = _env_int(
            "DB_KEEPALIVES_INTERVAL", 10, minimum=1, maximum=120
        )
        connect_args["keepalives_count"] = _env_int(
            "DB_KEEPALIVES_COUNT", 5, minimum=1, maximum=20
        )

        pool_size = _env_int("DB_POOL_SIZE", 20, minimum=1, maximum=200)
        max_overflow = _env_int("DB_MAX_OVERFLOW", 40, minimum=0, maximum=400)
    else:
        # SQLite local fallback
        connect_args["check_same_thread"] = False
        pool_size = _env_int("DB_POOL_SIZE", 10, minimum=1, maximum=100)
        max_overflow = _env_int("DB_MAX_OVERFLOW", 20, minimum=0, maximum=200)

    pool_timeout = _env_int("DB_POOL_TIMEOUT", 30, minimum=1, maximum=300)
    pool_recycle = _env_int("DB_POOL_RECYCLE", 1800, minimum=60, maximum=86400)

    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=pool_recycle,
        future=True,
        connect_args=connect_args,
    )


ParamsType = Union[None, Dict[str, Any], Tuple[Any, ...], Iterable[Any]]


def _adapt_qmark_params(sql: str, params: ParamsType) -> Tuple[str, Dict[str, Any]]:
    if params is None:
        return sql, {}

    if isinstance(params, dict):
        return sql, params

    seq = list(params)
    bind: Dict[str, Any] = {}
    out = []
    idx = 0
    for ch in sql:
        if ch == "?":
            key = f"p{idx}"
            out.append(f":{key}")
            bind[key] = seq[idx]
            idx += 1
        else:
            out.append(ch)

    return "".join(out), bind


class DBConn:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params: ParamsType = None):
        adapted_sql, bind = _adapt_qmark_params(sql, params)
        return self._conn.execute(text(adapted_sql), bind).mappings()

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception as e:
            # If the underlying connection is already broken (for example intermittent SSL/network issues),
            # SQLAlchemy may raise while rolling back/closing. Do not mask the real error.
            logger.warning("DB connection close failed: %s", e)


def connect(*, default_sqlite_db_file: str) -> DBConn:
    url = get_database_url(default_sqlite_db_file=default_sqlite_db_file)
    engine = get_engine(url)
    return DBConn(engine.connect())


def init_schema(*, default_sqlite_db_file: str) -> None:
    url = get_database_url(default_sqlite_db_file=default_sqlite_db_file)
    postgres = is_postgres_url(url)

    # Retry logic for transient DB startup latency.
    max_retries = _env_int("DB_INIT_RETRIES", 3, minimum=1, maximum=10)
    conn = None
    for attempt in range(1, max_retries + 1):
        try:
            conn = connect(default_sqlite_db_file=default_sqlite_db_file)
            break
        except Exception as e:
            if attempt < max_retries:
                wait = attempt * 5
                logger.warning(
                    "DB connection attempt %d/%d failed: %s - retrying in %ds",
                    attempt,
                    max_retries,
                    e,
                    wait,
                )
                time.sleep(wait)
            else:
                logger.error("DB connection failed after %d attempts: %s", max_retries, e)
                raise

    assert conn is not None
    try:
        if postgres:
            # Postgres: use BIGSERIAL for autoincrement ids.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sale_orders (
                    id BIGSERIAL PRIMARY KEY,
                    username TEXT,
                    dealer_name TEXT,
                    city TEXT,
                    order_id TEXT,
                    report_name TEXT,
                    generated_at TEXT,
                    order_type TEXT DEFAULT 'new'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS order_id_views (
                    id BIGSERIAL PRIMARY KEY,
                    username TEXT,
                    order_id TEXT,
                    viewed_at TEXT,
                    ip TEXT,
                    user_agent TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS issued_order_ids (
                    id BIGSERIAL PRIMARY KEY,
                    order_id TEXT UNIQUE,
                    given_to_name TEXT,
                    dealer_name TEXT,
                    city TEXT,
                    given_by_user TEXT,
                    given_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS counters (
                    month_year TEXT PRIMARY KEY,
                    counter INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS active_sessions (
                    username TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    issued_at TEXT,
                    ip TEXT,
                    user_agent TEXT
                )
                """
            )
        else:
            # SQLite: keep INTEGER PRIMARY KEY.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sale_orders (id INTEGER PRIMARY KEY,username TEXT,dealer_name TEXT,city TEXT,order_id TEXT,report_name TEXT,generated_at TEXT,order_type TEXT DEFAULT 'new')"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS order_id_views (id INTEGER PRIMARY KEY,username TEXT,order_id TEXT,viewed_at TEXT,ip TEXT,user_agent TEXT)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS issued_order_ids (id INTEGER PRIMARY KEY,order_id TEXT UNIQUE,given_to_name TEXT,dealer_name TEXT,city TEXT,given_by_user TEXT,given_at TEXT)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS counters (month_year TEXT PRIMARY KEY,counter INTEGER)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS active_sessions (username TEXT PRIMARY KEY,session_id TEXT NOT NULL,issued_at TEXT,ip TEXT,user_agent TEXT)"
            )

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sale_orders_generated_at ON sale_orders (generated_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sale_orders_username ON sale_orders (username)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sale_orders_order_id ON sale_orders (order_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sale_orders_dealer_city ON sale_orders (dealer_name, city)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_issued_order_ids_given_at ON issued_order_ids (given_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_order_id_views_viewed_at ON order_id_views (viewed_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_active_sessions_issued_at ON active_sessions (issued_at)"
        )

        conn.commit()
    finally:
        conn.close()

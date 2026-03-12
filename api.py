# ================================
# api.py - REST API Endpoints
# ================================
from flask import Blueprint, current_app, has_app_context, request, jsonify, send_file
from functools import wraps
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Set
from io import BytesIO
import jwt
import mimetypes
import os
import re
import uuid
import tempfile

import bcrypt
from werkzeug.utils import secure_filename

from db_utils import connect as db_connect
from generate_sale_order import prepare_data, write_report, generate_unique_order_id
from storage_utils import upload_file, download_file, delete_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_FILE = os.getenv('DATABASE_FILE', os.path.join(BASE_DIR, 'order_counter.db'))

api_bp = Blueprint('api', __name__, url_prefix='/api/v1')

# JWT Secret
JWT_SECRET = os.getenv("JWT_SECRET") or os.getenv("SECRET_KEY")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET or SECRET_KEY environment variable must be set")

def get_db_connection():
    db_file = DATABASE_FILE
    if has_app_context():
        db_file = current_app.config.get("DATABASE_FILE", DATABASE_FILE)
    return db_connect(default_sqlite_db_file=db_file)


def _date_prefix_days_ago(days: int) -> str:
    """Return YYYY-MM-DD date string for simple TEXT comparisons across SQLite/Postgres."""
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _request_int(
    name: str, default: int, *, minimum: Optional[int] = None, maximum: Optional[int] = None
) -> int:
    raw = request.args.get(name)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            value = default

    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _truncate_bcrypt_password(password: str) -> bytes:
    # bcrypt uses only first 72 bytes.
    return password.encode("utf-8")[:72]


def _check_bcrypt_password(hashed: str, password: str) -> bool:
    try:
        return bcrypt.checkpw(_truncate_bcrypt_password(password), hashed.encode("utf-8"))
    except Exception:
        return False


def _cors_allowed_origins() -> List[str]:
    raw = (os.getenv("CORS_ALLOWED_ORIGINS") or "").strip()
    if not raw:
        return ["*"]
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or ["*"]


@api_bp.before_request
def _cors_preflight():
    # Handle browser preflight requests.
    if request.method == "OPTIONS":
        return ("", 204)


@api_bp.after_request
def _cors_after(resp):
    origin = request.headers.get("Origin")
    if not origin:
        return resp

    allowed = _cors_allowed_origins()
    if "*" in allowed:
        resp.headers["Access-Control-Allow-Origin"] = "*"
    elif origin in allowed:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
    else:
        return resp

    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Authorization,Content-Type"
    resp.headers["Access-Control-Max-Age"] = "86400"
    resp.headers["Access-Control-Expose-Headers"] = "Content-Disposition"
    return resp


def _get_client_ip() -> str:
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or (request.remote_addr or "")


def _get_active_session_id(username: str) -> Optional[str]:
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT session_id FROM active_sessions WHERE username = ?",
            (username,),
        ).fetchone()
        return row["session_id"] if row else None
    finally:
        conn.close()


def _upsert_active_session(username: str, session_id: str) -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO active_sessions (username, session_id, issued_at, ip, user_agent)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                session_id = excluded.session_id,
                issued_at = excluded.issued_at,
                ip = excluded.ip,
                user_agent = excluded.user_agent
            """,
            (
                username,
                session_id,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                _get_client_ip(),
                (request.headers.get("User-Agent") or "")[:512],
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _bearer_token() -> Optional[str]:
    auth_header = request.headers.get("Authorization") or ""
    if auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return None


# ==================== JWT Authentication ====================
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = _bearer_token()
        
        if not token:
            return jsonify({'success': False, 'error': 'Token is missing', 'code': 'AUTH_REQUIRED'}), 401
        
        try:
            data = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
            current_user = data.get('username')
            sid = data.get('sid')
            if not current_user or not sid:
                return jsonify({'success': False, 'error': 'Invalid token', 'code': 'INVALID_TOKEN'}), 401

            try:
                expected_sid = _get_active_session_id(current_user)
            except Exception as e:
                # If DB is temporarily unavailable (network/SSL issues), don't treat it as an auth failure.
                current_app.logger.error("Auth session check failed (db error): %s", e)
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Auth temporarily unavailable, please retry",
                            "code": "DB_UNAVAILABLE",
                        }
                    ),
                    503,
                )
            if not expected_sid or expected_sid != sid:
                return jsonify({'success': False, 'error': 'Session revoked', 'code': 'SESSION_REVOKED'}), 401
        except jwt.ExpiredSignatureError:
            return jsonify({'success': False, 'error': 'Token expired', 'code': 'TOKEN_EXPIRED'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'success': False, 'error': 'Invalid token', 'code': 'INVALID_TOKEN'}), 401
        
        return f(current_user, *args, **kwargs)
    return decorated


def session_or_token_required(f):
    """Token-only auth (frontend is React on GitHub Pages)."""
    return token_required(f)


# ==================== Auth (for React / GitHub Pages) ====================
@api_bp.route("/auth/login", methods=["POST"])
def auth_login():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""

    if not username or not password:
        return jsonify({"success": False, "error": "Username and password required"}), 400

    users = (current_app.config.get("USERS_DICT") or {})
    hashed = users.get(username)
    if not hashed or not _check_bcrypt_password(hashed, password):
        return jsonify({"success": False, "error": "Invalid credentials"}), 401

    # Enforce single active session per user (same behavior as previous session-based login).
    session_id = uuid.uuid4().hex
    try:
        _upsert_active_session(username, session_id)
    except Exception as e:
        return jsonify({"success": False, "error": f"Login failed: {e}"}), 500

    expires_seconds = _env_int("JWT_EXPIRES_SECONDS", 8 * 60 * 60)
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=expires_seconds)
    token = jwt.encode(
        {
            "username": username,
            "sid": session_id,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        },
        JWT_SECRET,
        algorithm="HS256",
    )

    admin_users = [u.strip() for u in (os.getenv("ADMIN_USERS", "admin") or "").split(",") if u.strip()]
    return jsonify(
        {
            "success": True,
            "data": {
                "token": token,
                "token_type": "Bearer",
                "expires_at": exp.isoformat(),
                "username": username,
                "is_admin": username in admin_users,
            },
        }
    )


@api_bp.route("/auth/logout", methods=["POST"])
@token_required
def auth_logout(current_user):
    try:
        conn = get_db_connection()
        conn.execute("DELETE FROM active_sessions WHERE username = ?", (current_user,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/auth/me", methods=["GET"])
@session_or_token_required
def auth_me(current_user):
    admin_users = [u.strip() for u in (os.getenv("ADMIN_USERS", "admin") or "").split(",") if u.strip()]
    return jsonify({"success": True, "data": {"username": current_user, "is_admin": current_user in admin_users}})


def _admin_users() -> Set[str]:
    return {u.strip() for u in (os.getenv("ADMIN_USERS", "admin") or "").split(",") if u.strip()}


def _require_admin(current_user: str):
    if current_user not in _admin_users():
        return jsonify({"success": False, "error": "Admin access required"}), 403
    return None


def admin_required_api(f):
    @wraps(f)
    @session_or_token_required
    def decorated(current_user, *args, **kwargs):
        denied = _require_admin(current_user)
        if denied is not None:
            return denied
        return f(current_user, *args, **kwargs)

    return decorated


def _allowed_file(filename: str) -> bool:
    allowed = current_app.config.get("ALLOWED_EXTENSIONS") or {"xls", "xlsx"}
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed


def _reject_path_traversal(value: str) -> bool:
    return (".." in value) or ("/" in value) or ("\\" in value)


def _upload_folder() -> str:
    folder = current_app.config.get("UPLOAD_FOLDER") or os.path.join(BASE_DIR, "uploads")
    # Only create directory for local paths, not cloud storage URLs
    if not folder.startswith(("http://", "https://")):
        os.makedirs(folder, exist_ok=True)
    return folder


def _report_folder() -> str:
    folder = current_app.config.get("REPORT_FOLDER") or os.path.join(BASE_DIR, "reports")
    # Only create directory for local paths, not cloud storage URLs
    if not folder.startswith(("http://", "https://")):
        os.makedirs(folder, exist_ok=True)
    return folder


def _safe_report_name(prefix: str, *, suffix: str) -> str:
    safe_prefix = re.sub(r"[^a-zA-Z0-9_.-]+", "_", prefix or "REPORT").strip("_") or "REPORT"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{safe_prefix}_{timestamp}_{suffix}.xlsx"


def _looks_like_legacy_tmp_report_name(value: str) -> bool:
    if not value:
        return False
    return bool(re.fullmatch(r"tmp[a-z0-9]{4,}\.xlsx", value.strip(), flags=re.IGNORECASE))


def _derive_report_name_for_legacy_row(dealer_name: str, generated_at: str, order_type: str) -> Optional[str]:
    try:
        dt = datetime.strptime((generated_at or "").strip(), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

    safe_prefix = re.sub(r"[^a-zA-Z0-9_.-]+", "_", dealer_name or "REPORT").strip("_") or "REPORT"
    timestamp = dt.strftime("%Y%m%d_%H%M%S")
    suffix = "ADDITIONAL_ORDER" if (order_type or "").strip().lower() == "additional" else "SALE_ORDER"
    return f"{safe_prefix}_{timestamp}_{suffix}.xlsx"


def _fix_legacy_report_name(order: dict) -> None:
    current = (order.get("report_name") or "").strip()
    if not _looks_like_legacy_tmp_report_name(current):
        return

    derived = _derive_report_name_for_legacy_row(
        order.get("dealer_name") or "",
        order.get("generated_at") or "",
        order.get("order_type") or "",
    )
    if derived:
        order["report_name"] = derived


# ==================== Admin API (React) ====================
@api_bp.route("/admin/overview", methods=["GET"])
@admin_required_api
def admin_overview(current_user):
    try:
        conn = get_db_connection()

        total_orders = conn.execute("SELECT COUNT(*) as c FROM sale_orders").fetchone()["c"]
        today = datetime.now().strftime("%Y-%m-%d")
        today_orders = conn.execute(
            "SELECT COUNT(*) as c FROM sale_orders WHERE generated_at LIKE ?",
            (f"{today}%",),
        ).fetchone()["c"]
        issued_ids = conn.execute("SELECT COUNT(*) as c FROM issued_order_ids").fetchone()["c"]
        active_sessions = conn.execute("SELECT COUNT(*) as c FROM active_sessions").fetchone()["c"]

        recent_orders = conn.execute(
            """
            SELECT order_id, dealer_name, city, username, generated_at
            FROM sale_orders
            ORDER BY generated_at DESC
            LIMIT 10
            """
        ).fetchall()

        sessions = conn.execute(
            """
            SELECT username, session_id, issued_at, ip, user_agent
            FROM active_sessions
            ORDER BY issued_at DESC
            LIMIT 200
            """
        ).fetchall()

        conn.close()

        return jsonify(
            {
                "success": True,
                "data": {
                    "stats": {
                        "total_orders": total_orders,
                        "today_orders": today_orders,
                        "issued_ids": issued_ids,
                        "active_sessions": active_sessions,
                    },
                    "recent_orders": [dict(o) for o in recent_orders],
                    "sessions": [dict(s) for s in sessions],
                },
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/admin/users", methods=["GET"])
@admin_required_api
def admin_users(current_user):
    users = sorted((current_app.config.get("USERS_DICT") or {}).keys())
    admins = _admin_users()
    return jsonify(
        {
            "success": True,
            "data": [{"username": u, "role": ("admin" if u in admins else "user")} for u in users],
        }
    )


@api_bp.route("/admin/orders", methods=["GET"])
@admin_required_api
def admin_orders(current_user):
    """All orders with filtering + pagination (admin)."""
    try:
        conn = get_db_connection()

        page = _request_int("page", 1, minimum=1, maximum=1_000_000)
        per_page = _request_int("per_page", 20, minimum=1, maximum=200)
        offset = (page - 1) * per_page

        dealer_name = (request.args.get("dealer_name") or "").strip()
        city = (request.args.get("city") or "").strip()
        order_id = (request.args.get("order_id") or "").strip()
        username = (request.args.get("username") or "").strip()
        date_from = (request.args.get("date_from") or "").strip()
        date_to = (request.args.get("date_to") or "").strip()

        conditions = []
        params = []

        if username:
            conditions.append("username LIKE ?")
            params.append(f"%{username}%")
        if dealer_name:
            conditions.append("dealer_name LIKE ?")
            params.append(f"%{dealer_name}%")
        if city:
            conditions.append("city LIKE ?")
            params.append(f"%{city}%")
        if order_id:
            conditions.append("order_id LIKE ?")
            params.append(f"%{order_id}%")
        if date_from:
            conditions.append("generated_at >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("generated_at <= ?")
            params.append(f"{date_to} 23:59:59")

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        total = conn.execute(
            f"SELECT COUNT(*) as total FROM sale_orders WHERE {where_clause}",
            tuple(params),
        ).fetchone()["total"]

        query = f"""
            SELECT id, username, dealer_name, city, order_id, report_name, generated_at, order_type
            FROM sale_orders
            WHERE {where_clause}
            ORDER BY generated_at DESC
            LIMIT ? OFFSET ?
        """
        page_params = list(params) + [per_page, offset]
        rows = conn.execute(query, tuple(page_params)).fetchall()
        conn.close()

        orders_list = []
        for r in rows:
            item = dict(r)
            _fix_legacy_report_name(item)
            orders_list.append(item)

        return jsonify(
            {
                "success": True,
                "data": {
                    "orders": orders_list,
                    "pagination": {
                        "page": page,
                        "per_page": per_page,
                        "total": total,
                        "total_pages": (total + per_page - 1) // per_page,
                    },
                },
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/admin/sessions", methods=["GET"])
@admin_required_api
def admin_sessions(current_user):
    try:
        conn = get_db_connection()
        sessions = conn.execute(
            """
            SELECT username, session_id, issued_at, ip, user_agent
            FROM active_sessions
            ORDER BY issued_at DESC
            """
        ).fetchall()
        conn.close()
        return jsonify({"success": True, "data": [dict(s) for s in sessions]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/admin/sessions/revoke/<username>", methods=["POST"])
@admin_required_api
def admin_revoke_session(current_user, username):
    username = (username or "").strip()
    if not username:
        return jsonify({"success": False, "error": "username required"}), 400

    try:
        conn = get_db_connection()
        conn.execute("DELETE FROM active_sessions WHERE username = ?", (username,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "data": {"username": username}})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/admin/sessions/revoke-all", methods=["POST"])
@admin_required_api
def admin_revoke_all_sessions(current_user):
    try:
        conn = get_db_connection()
        conn.execute("DELETE FROM active_sessions")
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/admin/logs", methods=["GET"])
@admin_required_api
def admin_logs(current_user):
    lines = _env_int("ADMIN_LOG_LINES_DEFAULT", 500)
    try:
        requested = int(request.args.get("lines", lines))
    except ValueError:
        requested = lines

    requested = max(1, min(requested, 5000))
    log_file = os.getenv("LOG_FILE", os.path.join(BASE_DIR, "app.log"))

    if not os.path.exists(log_file):
        return jsonify({"success": True, "data": {"file": log_file, "lines": []}})

    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        tail = [ln.rstrip("\n") for ln in all_lines[-requested:]]
        return jsonify({"success": True, "data": {"file": log_file, "lines": tail}})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Upload + Report Generation (React) ====================
@api_bp.route("/uploads", methods=["POST"])
@session_or_token_required
def create_upload(current_user):
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"success": False, "error": "No file selected"}), 400

    if not _allowed_file(f.filename):
        return jsonify({"success": False, "error": "Invalid file type. Upload .xls or .xlsx"}), 400

    try:
        original_name = secure_filename(f.filename)
        upload_id = f"{uuid.uuid4().hex}_{original_name}"
        
        # Read file content
        file_content = f.read()
        size_bytes = len(file_content)
        
        # Determine content type
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if original_name.lower().endswith(".xls"):
            content_type = "application/vnd.ms-excel"
        
        # Upload to storage (Supabase or local)
        upload_file(file_content, f"uploads/{upload_id}", content_type)
        
        return jsonify(
            {
                "success": True,
                "data": {
                    "upload_id": upload_id,
                    "filename": original_name,
                    "size_bytes": size_bytes,
                    "uploaded_at": datetime.now().isoformat(),
                },
            }
        )
    except PermissionError as e:
        return jsonify({"success": False, "error": f"Upload access denied: {str(e)}"}), 403
    except Exception as e:
        return jsonify({"success": False, "error": f"Upload failed: {str(e)}"}), 500


@api_bp.route("/reports", methods=["POST"])
@session_or_token_required
def generate_report(current_user):
    payload = request.get_json(silent=True) or {}
    upload_id = (payload.get("upload_id") or "").strip()
    dealer_name = (payload.get("dealer_name") or "").strip()
    city = (payload.get("city") or "").strip()
    order_date_raw = (payload.get("order_date") or "").strip()  # expected YYYY-MM-DD
    freight_condition = (payload.get("freight_condition") or "").strip()
    custom_order_id = (payload.get("custom_order_id") or "").strip()
    is_additional_order = bool(payload.get("is_additional_order")) or bool(custom_order_id)

    if not upload_id:
        return jsonify({"success": False, "error": "upload_id is required"}), 400
    if _reject_path_traversal(upload_id):
        return jsonify({"success": False, "error": "Invalid upload_id"}), 400
    if not dealer_name or not city:
        return jsonify({"success": False, "error": "dealer_name and city are required"}), 400

    upload_storage_path = f"uploads/{upload_id}"

    order_date = ""
    if order_date_raw:
        try:
            order_date = datetime.strptime(order_date_raw, "%Y-%m-%d").strftime("%d-%m-%Y")
        except ValueError:
            return jsonify({"success": False, "error": "Invalid order_date (expected YYYY-MM-DD)"}), 400

    try:
        upload_file_data, _ = download_file(upload_storage_path)
    except FileNotFoundError:
        return jsonify({"success": False, "error": "Upload not found"}), 404
    except PermissionError as e:
        return jsonify({"success": False, "error": f"Upload access denied: {str(e)}"}), 403
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to read upload: {str(e)}"}), 500

    temp_upload_path = None
    temp_output_path = None
    temp_output_dir = None
    
    try:
        # Write uploaded file to temporary location for processing
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp.write(upload_file_data)
            temp_upload_path = tmp.name
        
        df, _, weight_map, hdmr_map, mdf_map, ply_map, pvc_map, wpc_map = prepare_data(temp_upload_path)

        if custom_order_id:
            order_id = custom_order_id
        else:
            order_id = generate_unique_order_id()

        suffix = "ADDITIONAL_ORDER" if is_additional_order else "SALE_ORDER"
        report_name = _safe_report_name(dealer_name, suffix=suffix)
        
        temp_output_dir = tempfile.mkdtemp(prefix="report_")
        temp_output_path = os.path.join(temp_output_dir, report_name)

        write_report(
            df,
            temp_output_path,
            weight_map,
            hdmr_map,
            mdf_map,
            ply_map,
            pvc_map,
            wpc_map,
            current_user,
            dealer_name,
            city,
            order_date,
            freight_condition,
            custom_order_id=order_id,
            is_additional_order=is_additional_order,
        )
        
        # Upload report to storage
        with open(temp_output_path, 'rb') as f:
            report_data = f.read()
        
        upload_file(
            report_data,
            f"reports/{report_name}",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
        # Clean up uploaded file and temporary files
        delete_file(upload_storage_path)
        if temp_upload_path and os.path.exists(temp_upload_path):
            os.remove(temp_upload_path)
        if temp_output_path and os.path.exists(temp_output_path):
            os.remove(temp_output_path)
        if temp_output_dir and os.path.exists(temp_output_dir):
            try:
                os.rmdir(temp_output_dir)
            except Exception:
                pass

        return jsonify(
            {
                "success": True,
                "data": {
                    "order_id": order_id,
                    "report_name": report_name,
                },
            }
        )
    except PermissionError as e:
        # Clean up on error
        delete_file(upload_storage_path)
        if temp_upload_path and os.path.exists(temp_upload_path):
            try:
                os.remove(temp_upload_path)
            except:
                pass
        if temp_output_path and os.path.exists(temp_output_path):
            try:
                os.remove(temp_output_path)
            except:
                pass
        if temp_output_dir and os.path.exists(temp_output_dir):
            try:
                os.rmdir(temp_output_dir)
            except Exception:
                pass
        return jsonify({"success": False, "error": f"Storage access denied: {str(e)}"}), 403
    except Exception as e:
        # Clean up on error
        delete_file(upload_storage_path)
        if temp_upload_path and os.path.exists(temp_upload_path):
            try:
                os.remove(temp_upload_path)
            except:
                pass
        if temp_output_path and os.path.exists(temp_output_path):
            try:
                os.remove(temp_output_path)
            except:
                pass
        if temp_output_dir and os.path.exists(temp_output_dir):
            try:
                os.rmdir(temp_output_dir)
            except Exception:
                pass
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/reports/<path:report_name>", methods=["GET"])
@session_or_token_required
def download_generated_report(current_user, report_name):
    if not report_name or _reject_path_traversal(report_name):
        return jsonify({"success": False, "error": "Invalid report_name"}), 400

    safe_name = os.path.basename(report_name)
    if safe_name != report_name:
        return jsonify({"success": False, "error": "Invalid report_name"}), 400

    report_storage_path = f"reports/{safe_name}"

    try:
        file_data, filename = download_file(report_storage_path)
        mimetype, _ = mimetypes.guess_type(filename)
        return send_file(
            BytesIO(file_data),
            as_attachment=True,
            download_name=filename,
            mimetype=mimetype or "application/octet-stream",
        )
    except FileNotFoundError:
        return jsonify({"success": False, "error": "Report not found"}), 404
    except PermissionError as e:
        return jsonify({"success": False, "error": f"Report access denied: {str(e)}"}), 403
    except Exception as e:
        return jsonify({"success": False, "error": f"Download failed: {str(e)}"}), 500


@api_bp.route("/order-ids/status", methods=["GET"])
@session_or_token_required
def order_id_status(current_user):
    try:
        conn = get_db_connection()
        row = conn.execute(
            """
            SELECT order_id
            FROM (
                SELECT order_id, generated_at as ts FROM sale_orders
                UNION ALL
                SELECT order_id, given_at as ts FROM issued_order_ids
            ) q
            ORDER BY ts DESC
            LIMIT 1
            """
        ).fetchone()
        latest_id = row["order_id"] if row else None

        recent_orders = conn.execute(
            """
            SELECT order_id, dealer_name, city, generated_at
            FROM sale_orders
            ORDER BY generated_at DESC
            LIMIT 10
            """
        ).fetchall()
        conn.close()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    # Keep same behavior as existing UI.
    current_month_year = datetime.now().strftime("%m-%y")
    suggested = f"{current_month_year}-00001"
    if latest_id:
        try:
            parts = latest_id.split("-")
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                month_year = f"{parts[0]}-{parts[1]}"
                num_part = parts[2]
                next_num = int(num_part) + 1 if month_year == current_month_year else 1
                suggested = f"{current_month_year}-{next_num:05d}"
        except Exception:
            pass

    return jsonify(
        {
            "success": True,
            "data": {
                "latest_id": latest_id,
                "suggested_id": suggested,
                "recent_orders": [dict(o) for o in recent_orders],
            },
        }
    )


@api_bp.route("/issued-ids", methods=["POST"])
@session_or_token_required
def create_issued_id(current_user):
    payload = request.get_json(silent=True) or {}
    order_id = (payload.get("order_id") or "").strip()
    given_to_name = (payload.get("given_to_name") or "").strip()
    dealer_name = (payload.get("dealer_name") or "").strip()
    city = (payload.get("city") or "").strip()

    if not order_id or not given_to_name:
        return jsonify({"success": False, "error": "order_id and given_to_name are required"}), 400

    try:
        conn = get_db_connection()
        existing = conn.execute(
            "SELECT order_id FROM issued_order_ids WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if existing:
            conn.close()
            return jsonify({"success": False, "error": "Order ID already issued"}), 409

        conn.execute(
            """
            INSERT INTO issued_order_ids (order_id, given_to_name, dealer_name, city, given_by_user, given_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (order_id, given_to_name, dealer_name, city, current_user, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        conn.close()
        return jsonify({"success": True, "data": {"order_id": order_id, "given_to_name": given_to_name}})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Dashboard Stats API ====================
@api_bp.route('/dashboard/stats', methods=['GET'])
@session_or_token_required
def get_dashboard_stats(current_user):
    """Get comprehensive dashboard statistics"""
    try:
        conn = get_db_connection()
        
        # Total orders
        total_orders = conn.execute("SELECT COUNT(*) as count FROM sale_orders").fetchone()['count']
        
        # User's orders
        user_orders = conn.execute(
            "SELECT COUNT(*) as count FROM sale_orders WHERE username = ?", 
            (current_user,)
        ).fetchone()['count']
        
        # Today's orders
        today = datetime.now().strftime('%Y-%m-%d')
        today_orders = conn.execute(
            "SELECT COUNT(*) as count FROM sale_orders WHERE generated_at LIKE ?",
            (f"{today}%",)
        ).fetchone()['count']
        
        # This month's orders
        current_month = datetime.now().strftime('%Y-%m')
        month_orders = conn.execute(
            "SELECT COUNT(*) as count FROM sale_orders WHERE generated_at LIKE ?",
            (f"{current_month}%",)
        ).fetchone()['count']
        
        # Top dealers (all time)
        top_dealers = conn.execute("""
            SELECT dealer_name, city, COUNT(*) as order_count 
            FROM sale_orders 
            GROUP BY dealer_name, city 
            ORDER BY order_count DESC 
            LIMIT 10
        """).fetchall()
        
        # Top cities
        top_cities = conn.execute("""
            SELECT city, COUNT(*) as order_count 
            FROM sale_orders 
            GROUP BY city 
            ORDER BY order_count DESC 
            LIMIT 10
        """).fetchall()
        
        # Orders by month (last 12 months)
        since_12m = _date_prefix_days_ago(365)
        monthly_orders = conn.execute("""
            SELECT 
                substr(generated_at, 1, 7) as month,
                COUNT(*) as count
            FROM sale_orders 
            WHERE generated_at >= ?
            GROUP BY month 
            ORDER BY month ASC
        """, (since_12m,)).fetchall()
        
        # Orders by user
        orders_by_user = conn.execute("""
            SELECT username, COUNT(*) as count 
            FROM sale_orders 
            GROUP BY username 
            ORDER BY count DESC
        """).fetchall()
        
        # Recent activity
        recent_orders = conn.execute("""
            SELECT order_id, dealer_name, city, username, generated_at 
            FROM sale_orders 
            ORDER BY generated_at DESC 
            LIMIT 20
        """).fetchall()
        
        # Issued order IDs count
        issued_ids = conn.execute("SELECT COUNT(*) as count FROM issued_order_ids").fetchone()['count']
        
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {
                'overview': {
                    'total_orders': total_orders,
                    'user_orders': user_orders,
                    'today_orders': today_orders,
                    'month_orders': month_orders,
                    'issued_ids': issued_ids
                },
                'top_dealers': [dict(d) for d in top_dealers],
                'top_cities': [dict(c) for c in top_cities],
                'monthly_orders': [dict(m) for m in monthly_orders],
                'orders_by_user': [dict(u) for u in orders_by_user],
                'recent_orders': [dict(o) for o in recent_orders]
            },
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/dashboard/chart-data', methods=['GET'])
@session_or_token_required
def get_chart_data(current_user):
    """Get chart-specific data for dashboard"""
    try:
        conn = get_db_connection()
        chart_type = request.args.get('type', 'monthly')
        
        if chart_type == 'monthly':
            since_12m = _date_prefix_days_ago(365)
            # Orders per month
            data = conn.execute("""
                SELECT 
                    substr(generated_at, 1, 7) as label,
                    COUNT(*) as value
                FROM sale_orders 
                WHERE generated_at >= ?
                GROUP BY label 
                ORDER BY label ASC
            """, (since_12m,)).fetchall()
        elif chart_type == 'daily':
            since_30d = _date_prefix_days_ago(30)
            # Orders per day (last 30 days)
            data = conn.execute("""
                SELECT 
                    substr(generated_at, 1, 10) as label,
                    COUNT(*) as value
                FROM sale_orders 
                WHERE generated_at >= ?
                GROUP BY label 
                ORDER BY label ASC
            """, (since_30d,)).fetchall()
        elif chart_type == 'city':
            # Orders by city
            data = conn.execute("""
                SELECT city as label, COUNT(*) as value 
                FROM sale_orders 
                GROUP BY city 
                ORDER BY value DESC 
                LIMIT 10
            """).fetchall()
        elif chart_type == 'user':
            # Orders by user
            data = conn.execute("""
                SELECT username as label, COUNT(*) as value 
                FROM sale_orders 
                GROUP BY username 
                ORDER BY value DESC
            """).fetchall()
        else:
            data = []
        
        conn.close()
        
        return jsonify({
            'success': True,
            'chart_type': chart_type,
            'labels': [d['label'] for d in data],
            'values': [d['value'] for d in data]
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== Order History API ====================
@api_bp.route('/orders', methods=['GET'])
@session_or_token_required
def get_orders(current_user):
    """Get orders with filtering and pagination"""
    try:
        conn = get_db_connection()
        
        # Pagination
        page = _request_int("page", 1, minimum=1, maximum=1_000_000)
        per_page = _request_int("per_page", 20, minimum=1, maximum=200)
        offset = (page - 1) * per_page
        
        # Filters
        dealer_name = (request.args.get("dealer_name") or "").strip()
        city = (request.args.get("city") or "").strip()
        order_id = (request.args.get("order_id") or "").strip()
        date_from = (request.args.get("date_from") or "").strip()
        date_to = (request.args.get("date_to") or "").strip()
        username = (request.args.get("username") or "").strip()
        only_mine = request.args.get('only_mine', 'false').lower() == 'true'
        
        # Build query
        conditions = []
        params = []
        
        if only_mine:
            conditions.append("username = ?")
            params.append(current_user)
        elif username:
            conditions.append("username LIKE ?")
            params.append(f"%{username}%")
        
        if dealer_name:
            conditions.append("dealer_name LIKE ?")
            params.append(f"%{dealer_name}%")
        
        if city:
            conditions.append("city LIKE ?")
            params.append(f"%{city}%")
        
        if order_id:
            conditions.append("order_id LIKE ?")
            params.append(f"%{order_id}%")
        
        if date_from:
            conditions.append("generated_at >= ?")
            params.append(date_from)
        
        if date_to:
            conditions.append("generated_at <= ?")
            params.append(f"{date_to} 23:59:59")
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        # Get total count
        count_query = f"SELECT COUNT(*) as total FROM sale_orders WHERE {where_clause}"
        total = conn.execute(count_query, tuple(params)).fetchone()['total']
        
        # Get paginated results
        query = f"""
            SELECT id, username, dealer_name, city, order_id, report_name, generated_at, order_type
            FROM sale_orders 
            WHERE {where_clause}
            ORDER BY generated_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([per_page, offset])
        orders = conn.execute(query, tuple(params)).fetchall()
        
        conn.close()

        orders_list = []
        for o in orders:
            item = dict(o)
            _fix_legacy_report_name(item)
            orders_list.append(item)
        
        return jsonify({
            'success': True,
            'data': {
                'orders': orders_list,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'total_pages': (total + per_page - 1) // per_page
                }
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/orders/<int:order_id>', methods=['GET'])
@session_or_token_required
def get_order_detail(current_user, order_id):
    """Get single order details"""
    try:
        conn = get_db_connection()
        order = conn.execute(
            "SELECT * FROM sale_orders WHERE id = ?", 
            (order_id,)
        ).fetchone()
        conn.close()
        
        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404
        
        return jsonify({'success': True, 'data': dict(order)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/orders/search', methods=['GET'])
@session_or_token_required
def search_orders(current_user):
    """Full-text search across orders"""
    try:
        query = request.args.get('q', '').strip()
        if len(query) < 2:
            return jsonify({'success': False, 'error': 'Query must be at least 2 characters'}), 400
        
        conn = get_db_connection()
        
        results = conn.execute("""
            SELECT id, username, dealer_name, city, order_id, report_name, generated_at 
            FROM sale_orders 
            WHERE dealer_name LIKE ? OR city LIKE ? OR order_id LIKE ? OR username LIKE ?
            ORDER BY generated_at DESC
            LIMIT 50
        """, (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%")).fetchall()
        
        conn.close()
        
        return jsonify({
            'success': True,
            'query': query,
            'results': [dict(r) for r in results],
            'count': len(results)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== Export API ====================
@api_bp.route('/orders/export', methods=['GET'])
@session_or_token_required
def export_orders(current_user):
    """Export orders data as JSON (can be converted to Excel client-side)"""
    try:
        conn = get_db_connection()
        
        # Same filters as get_orders
        date_from = request.args.get('date_from', '')
        date_to = request.args.get('date_to', '')
        only_mine = request.args.get('only_mine', 'false').lower() == 'true'
        
        conditions = []
        params = []
        
        if only_mine:
            conditions.append("username = ?")
            params.append(current_user)
        
        if date_from:
            conditions.append("generated_at >= ?")
            params.append(date_from)
        
        if date_to:
            conditions.append("generated_at <= ?")
            params.append(f"{date_to} 23:59:59")
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        orders = conn.execute(f"""
            SELECT order_id, dealer_name, city, username, generated_at 
            FROM sale_orders 
            WHERE {where_clause}
            ORDER BY generated_at DESC
        """, tuple(params)).fetchall()
        
        conn.close()
        
        return jsonify({
            'success': True,
            'data': [dict(o) for o in orders],
            'count': len(orders),
            'exported_at': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== Issued Order IDs API ====================
@api_bp.route('/issued-ids', methods=['GET'])
@session_or_token_required
def get_issued_ids(current_user):
    """Get issued order IDs with pagination"""
    try:
        conn = get_db_connection()
        
        page = _request_int("page", 1, minimum=1, maximum=1_000_000)
        per_page = _request_int("per_page", 20, minimum=1, maximum=200)
        offset = (page - 1) * per_page
        
        total = conn.execute("SELECT COUNT(*) as count FROM issued_order_ids").fetchone()['count']
        
        issued = conn.execute("""
            SELECT * FROM issued_order_ids 
            ORDER BY given_at DESC 
            LIMIT ? OFFSET ?
        """, (per_page, offset)).fetchall()
        
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {
                'issued_ids': [dict(i) for i in issued],
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'total_pages': (total + per_page - 1) // per_page
                }
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== Health Check ====================
@api_bp.route('/ready', methods=['GET'])
def readiness_check():
    """Readiness endpoint with optional DB check."""
    if not _env_bool("READINESS_CHECK_DB", default=True):
        return jsonify(
            {
                "status": "ready",
                "checks": {"database": "skipped"},
                "timestamp": datetime.now().isoformat(),
            }
        )

    conn = None
    try:
        conn = get_db_connection()
        conn.execute("SELECT 1").fetchone()
    except Exception as e:
        return (
            jsonify(
                {
                    "status": "not_ready",
                    "checks": {"database": "error"},
                    "error": str(e),
                    "timestamp": datetime.now().isoformat(),
                }
            ),
            503,
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return jsonify(
        {
            "status": "ready",
            "checks": {"database": "ok"},
            "timestamp": datetime.now().isoformat(),
        }
    )


@api_bp.route('/health', methods=['GET'])
def health_check():
    """API health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'version': '1.0.0',
        'timestamp': datetime.now().isoformat()
    })

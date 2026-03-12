"""
Supabase Storage Utility Module

Provides functions to upload, download, and manage files in Supabase Storage.
Automatically detects whether to use local storage or Supabase based on configuration.
"""
import base64
import json
import os
import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _get_supabase_key() -> Optional[str]:
    """
    Read Supabase API key from environment.

    Prefer SUPABASE_KEY (kept for backwards compatibility with existing deployments),
    but also accept SUPABASE_SERVICE_ROLE_KEY as an explicit alternative.
    """
    return os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")


def _is_cloud_storage() -> bool:
    """Check if Supabase cloud storage is configured."""
    return bool(os.getenv("SUPABASE_URL") and _get_supabase_key())


def _get_supabase_client():
    """Get Supabase client instance."""
    try:
        from supabase import create_client, Client
        url = os.getenv("SUPABASE_URL")
        key = _get_supabase_key()
        if not url or not key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY (or SUPABASE_SERVICE_ROLE_KEY) must be set")
        return create_client(url, key)
    except ImportError:
        raise ImportError("supabase package not installed. Run: pip install supabase")


def _get_bucket_name() -> str:
    """Get Supabase storage bucket name from environment."""
    return os.getenv("SUPABASE_STORAGE_BUCKET", "sale-orders")


def _decode_jwt_payload(token: str) -> Optional[dict]:
    if not token or token.count(".") != 2:
        return None

    parts = token.split(".")
    if len(parts) != 3:
        return None

    payload_b64 = parts[1]
    # Base64url without padding -> add padding back.
    payload_b64 += "=" * ((4 - (len(payload_b64) % 4)) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def _supabase_key_role(key: Optional[str]) -> str:
    payload = _decode_jwt_payload(key or "")
    role = (payload or {}).get("role")
    if isinstance(role, str) and role.strip():
        return role.strip()
    return "unknown"


def log_storage_startup() -> None:
    """
    Emit a safe, one-time startup log for storage configuration.

    This helps debug "Report not found" vs permission/path issues without logging secrets.
    """
    if getattr(log_storage_startup, "_logged", False):
        return

    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = _get_supabase_key()
    bucket = _get_bucket_name()

    if not url or not key:
        logger.info("Storage: local filesystem mode (Supabase not fully configured).")
        logger.info("Storage: SUPABASE_URL set=%s, SUPABASE_KEY set=%s, bucket='%s'", bool(url), bool(key), bucket)
        log_storage_startup._logged = True
        return

    role = _supabase_key_role(key)
    logger.info("Storage: Supabase mode enabled (bucket='%s', key_role='%s').", bucket, role)
    if role != "service_role":
        logger.warning(
            "Storage: SUPABASE_KEY is not a service_role key (detected key_role='%s'). "
            "Downloads/listing may fail with 403 depending on Storage policies. "
            "Set SUPABASE_KEY to your SERVICE_ROLE_KEY on the backend (never in frontend).",
            role,
        )

    log_storage_startup._logged = True


def _normalize_remote_path(remote_path: str, bucket: str) -> str:
    path = (remote_path or "").strip()
    if not path:
        return path

    # Supabase Storage uses forward slashes for object keys.
    path = path.replace("\\", "/").lstrip("/")

    bucket_prefix = f"{bucket}/"
    if path.startswith(bucket_prefix):
        logger.warning(
            "Supabase path normalization: remote_path '%s' includes bucket prefix '%s' (stripping).",
            remote_path,
            bucket_prefix,
        )
        path = path[len(bucket_prefix):]

    return path


def _local_full_path(remote_path: str) -> str:
    if os.path.isabs(remote_path):
        return remote_path
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, remote_path)


def _extract_status_code(exc: Exception) -> Optional[int]:
    for attr in ("status_code", "status", "statusCode", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())

    response = getattr(exc, "response", None)
    if response is not None:
        for attr in ("status_code", "status"):
            value = getattr(response, attr, None)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())

    # Sometimes the error payload is stored in args as a dict.
    for arg in getattr(exc, "args", []):
        if isinstance(arg, dict):
            for key in ("status_code", "status", "statusCode", "code"):
                value = arg.get(key)
                if isinstance(value, int):
                    return value
                if isinstance(value, str) and value.strip().isdigit():
                    return int(value.strip())

    # Best-effort fallback: look for common HTTP codes in the message.
    msg = str(exc) or ""
    m = re.search(r"\b(401|403|404)\b", msg)
    if m:
        return int(m.group(1))

    return None


def _classify_storage_error(exc: Exception) -> str:
    status = _extract_status_code(exc)
    msg = (str(exc) or "").lower()

    permission_markers = (
        "unauthorized",
        "forbidden",
        "not authorized",
        "permission denied",
        "insufficient permissions",
        "rls",
        "jwt",
        "invalid api key",
    )
    not_found_markers = (
        "not found",
        "object not found",
        "no such file",
        "does not exist",
    )

    if status in (401, 403):
        return "permission"
    if status == 404:
        # Some storage setups can surface permission issues as 404 to avoid leaking existence.
        if any(m in msg for m in permission_markers):
            return "permission"
        return "not_found"

    if any(m in msg for m in permission_markers):
        return "permission"
    if any(m in msg for m in not_found_markers) or " 404" in msg or "404 " in msg:
        return "not_found"

    return "unknown"


def upload_file(file_data: bytes, remote_path: str, content_type: str = "application/octet-stream") -> str:
    """
    Upload a file to Supabase Storage or local filesystem.
    
    Args:
        file_data: File content as bytes
        remote_path: Path within the storage bucket (e.g., "uploads/file.xlsx")
        content_type: MIME type of the file
    
    Returns:
        str: Full path or URL to the uploaded file
    
    Raises:
        Exception: If upload fails
    """
    if _is_cloud_storage():
        bucket = _get_bucket_name()
        normalized_path = _normalize_remote_path(remote_path, bucket)
        try:
            client = _get_supabase_client()

            logger.info(f"Uploading to Supabase bucket '{bucket}' at path '{normalized_path}'")
            
            # Upload to Supabase Storage
            response = client.storage.from_(bucket).upload(
                path=normalized_path,
                file=file_data,
                file_options={"content-type": content_type, "upsert": "true"}
            )
            
            logger.info(f"Successfully uploaded file to Supabase Storage: {bucket}/{normalized_path}")
            return normalized_path
        except Exception as e:
            category = _classify_storage_error(e)
            logger.error(f"Failed to upload to Supabase bucket '{bucket}' at '{normalized_path}': {e}")
            if category == "permission":
                raise PermissionError("Supabase upload forbidden (check key/policies).") from e
            raise Exception(f"Supabase upload failed: {str(e)}") from e
    else:
        # Fallback to local filesystem
        full_path = _local_full_path(remote_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        with open(full_path, 'wb') as f:
            f.write(file_data)
        
        logger.info(f"Saved file locally: {full_path}")
        return full_path


def download_file(remote_path: str) -> Tuple[bytes, str]:
    """
    Download a file from Supabase Storage or local filesystem.
    
    Args:
        remote_path: Path within the storage bucket or local path
    
    Returns:
        Tuple[bytes, str]: (file_data, filename)
    
    Raises:
        FileNotFoundError: If file doesn't exist
        Exception: If download fails
    """
    if _is_cloud_storage():
        bucket = _get_bucket_name()
        normalized_path = _normalize_remote_path(remote_path, bucket)
        try:
            client = _get_supabase_client()

            logger.info(f"Downloading from Supabase bucket '{bucket}' at path '{normalized_path}'")
            
            # Download from Supabase Storage
            file_data = client.storage.from_(bucket).download(normalized_path)
            filename = os.path.basename(normalized_path)
            
            logger.info(f"Successfully downloaded file from Supabase Storage: {bucket}/{normalized_path} ({len(file_data)} bytes)")
            return (file_data, filename)
        except Exception as e:
            category = _classify_storage_error(e)
            logger.error(f"Failed to download from Supabase bucket '{bucket}' at '{normalized_path}': {e}")
            if category == "permission":
                raise PermissionError(
                    "Supabase download forbidden (check Storage policies or use service_role key on backend)."
                ) from e
            if category == "not_found":
                raise FileNotFoundError(
                    f"File not found in Supabase bucket '{bucket}': {normalized_path}"
                ) from e
            raise Exception(f"Supabase download failed: {str(e)}") from e
    else:
        # Fallback to local filesystem
        full_path = _local_full_path(remote_path)
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"File not found: {full_path}")

        with open(full_path, 'rb') as f:
            file_data = f.read()
        
        filename = os.path.basename(full_path)
        logger.info(f"Read file locally: {full_path}")
        return (file_data, filename)


def delete_file(remote_path: str) -> bool:
    """
    Delete a file from Supabase Storage or local filesystem.
    
    Args:
        remote_path: Path within the storage bucket or local path
    
    Returns:
        bool: True if successful, False otherwise
    """
    if _is_cloud_storage():
        bucket = _get_bucket_name()
        normalized_path = _normalize_remote_path(remote_path, bucket)
        try:
            client = _get_supabase_client()

            logger.info(f"Deleting from Supabase bucket '{bucket}' at path '{normalized_path}'")
            
            # Delete from Supabase Storage
            client.storage.from_(bucket).remove([normalized_path])
            logger.info(f"Successfully deleted file from Supabase Storage: {bucket}/{normalized_path}")
            return True
        except Exception as e:
            logger.warning(f"Failed to delete from Supabase bucket '{bucket}' at '{normalized_path}': {e}")
            return False
    else:
        # Fallback to local filesystem
        try:
            full_path = _local_full_path(remote_path)
            if os.path.exists(full_path):
                os.remove(full_path)
                logger.info(f"Deleted local file: {full_path}")
                return True
        except Exception as e:
            logger.warning(f"Failed to delete local file: {e}")
        return False


def get_public_url(remote_path: str) -> Optional[str]:
    """
    Get public URL for a file in Supabase Storage.
    
    Args:
        remote_path: Path within the storage bucket
    
    Returns:
        Optional[str]: Public URL or None if not using cloud storage
    """
    if _is_cloud_storage():
        bucket = _get_bucket_name()
        normalized_path = _normalize_remote_path(remote_path, bucket)
        try:
            client = _get_supabase_client()
            # Get public URL
            url = client.storage.from_(bucket).get_public_url(normalized_path)
            return url
        except Exception as e:
            logger.error(f"Failed to get public URL: {e}")
            return None
    return None


def file_exists(remote_path: str) -> bool:
    """
    Check if a file exists in Supabase Storage or local filesystem.
    
    Args:
        remote_path: Path within the storage bucket or local path
    
    Returns:
        bool: True if file exists, False otherwise
    """
    if _is_cloud_storage():
        bucket = _get_bucket_name()
        normalized_path = _normalize_remote_path(remote_path, bucket)
        try:
            client = _get_supabase_client()
            
            # List files and check if path exists
            dir_path = os.path.dirname(normalized_path)
            files = client.storage.from_(bucket).list(dir_path or "")
            filename = os.path.basename(normalized_path)
            return any(f.get("name") == filename for f in files)
        except Exception as e:
            category = _classify_storage_error(e)
            logger.warning(f"Error checking file existence in Supabase: {e}")
            if category == "permission":
                raise PermissionError(
                    "Supabase list forbidden while checking file existence (check key/policies)."
                ) from e
            raise
    else:
        return os.path.exists(_local_full_path(remote_path))

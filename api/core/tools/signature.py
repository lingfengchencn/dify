import base64
import hashlib
import hmac
import os
import time

from configs import dify_config
from extensions.ext_storage import storage


def sign_tool_file(tool_file_id: str, extension: str, *, storage_key: str | None = None) -> str:
    """Return a public URL for a tool file based on configuration."""
    if dify_config.FILES_URL_TYPE == "cloud":
        resolved_storage_key = storage_key or _load_tool_file_storage_key(tool_file_id)
        if not resolved_storage_key:
            raise ValueError(f"Unable to resolve storage key for tool file {tool_file_id}")
        try:
            return storage.get_url(resolved_storage_key)
        except NotImplementedError as exc:
            raise NotImplementedError(
                "Current storage backend cannot expose public URLs; switch FILES_URL_TYPE to 'local'."
            ) from exc

    return _build_signed_local_url(tool_file_id, extension)


def verify_tool_file_signature(file_id: str, timestamp: str, nonce: str, sign: str) -> bool:
    """
    verify signature
    """
    data_to_sign = f"file-preview|{file_id}|{timestamp}|{nonce}"
    secret_key = dify_config.SECRET_KEY.encode() if dify_config.SECRET_KEY else b""
    recalculated_sign = hmac.new(secret_key, data_to_sign.encode(), hashlib.sha256).digest()
    recalculated_encoded_sign = base64.urlsafe_b64encode(recalculated_sign).decode()

    # verify signature
    if sign != recalculated_encoded_sign:
        return False

    current_time = int(time.time())
    return current_time - int(timestamp) <= dify_config.FILES_ACCESS_TIMEOUT


def _build_signed_local_url(tool_file_id: str, extension: str) -> str:
    base_url = dify_config.INTERNAL_FILES_URL or dify_config.FILES_URL
    file_preview_url = f"{base_url}/files/tools/{tool_file_id}{extension}"

    timestamp = str(int(time.time()))
    nonce = os.urandom(16).hex()
    data_to_sign = f"file-preview|{tool_file_id}|{timestamp}|{nonce}"
    secret_key = dify_config.SECRET_KEY.encode() if dify_config.SECRET_KEY else b""
    sign = hmac.new(secret_key, data_to_sign.encode(), hashlib.sha256).digest()
    encoded_sign = base64.urlsafe_b64encode(sign).decode()

    return f"{file_preview_url}?timestamp={timestamp}&nonce={nonce}&sign={encoded_sign}"


def _load_tool_file_storage_key(tool_file_id: str) -> str | None:
    try:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from extensions.ext_database import db as global_db
        from models.tools import ToolFile
    except ImportError:  # pragma: no cover - import guard
        return None

    with Session(global_db.engine, expire_on_commit=False) as session:
        result = session.execute(select(ToolFile.file_key).where(ToolFile.id == tool_file_id))
        return result.scalar_one_or_none()

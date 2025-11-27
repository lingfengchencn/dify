import logging
from collections.abc import Generator
from datetime import date, datetime
from decimal import Decimal
from mimetypes import guess_extension
from uuid import UUID

import numpy as np
import pytz

from core.file import File, FileTransferMethod, FileType
from core.tools.entities.tool_entities import ToolInvokeMessage
from core.tools.signature import sign_tool_file
from core.tools.signature import sign_tool_file
from core.tools.tool_file_manager import ToolFileManager
from libs.login import current_user
from models import Account

logger = logging.getLogger(__name__)


def safe_json_value(v):
    if isinstance(v, datetime):
        tz_name = "UTC"
        if isinstance(current_user, Account) and current_user.timezone is not None:
            tz_name = current_user.timezone
        return v.astimezone(pytz.timezone(tz_name)).isoformat()
    elif isinstance(v, date):
        return v.isoformat()
    elif isinstance(v, UUID):
        return str(v)
    elif isinstance(v, Decimal):
        return float(v)
    elif isinstance(v, bytes):
        try:
            return v.decode("utf-8")
        except UnicodeDecodeError:
            return v.hex()
    elif isinstance(v, memoryview):
        return v.tobytes().hex()
    elif isinstance(v, np.ndarray):
        return v.tolist()
    elif isinstance(v, dict):
        return safe_json_dict(v)
    elif isinstance(v, list | tuple | set):
        return [safe_json_value(i) for i in v]
    else:
        return v


def safe_json_dict(d: dict):
    if not isinstance(d, dict):
        raise TypeError(
            "safe_json_dict() expects a dictionary (dict) as input")
    return {k: safe_json_value(v) for k, v in d.items()}


class ToolFileMessageTransformer:
    @classmethod
    def transform_tool_invoke_messages(
        cls,
        messages: Generator[ToolInvokeMessage, None, None],
        user_id: str,
        tenant_id: str,
        conversation_id: str | None = None,
    ) -> Generator[ToolInvokeMessage, None, None]:
        """
        Transform tool message and handle file download
        """
        for message in messages:
            if message.type in {ToolInvokeMessage.MessageType.TEXT, ToolInvokeMessage.MessageType.LINK}:
                yield message
            elif message.type == ToolInvokeMessage.MessageType.IMAGE and isinstance(
                message.message, ToolInvokeMessage.TextMessage
            ):
                # try to download image
                try:
                    assert isinstance(
                        message.message, ToolInvokeMessage.TextMessage)
                    tool_file_manager = ToolFileManager()
                    tool_file = tool_file_manager.create_file_by_url(
                        user_id=user_id,
                        tenant_id=tenant_id,
                        file_url=message.message.text,
                        conversation_id=conversation_id,
                    )

                    url = sign_tool_file(
                        tool_file_id=tool_file.id,
                        extension=guess_extension(
                            tool_file.mimetype) or ".png",
                        storage_key=tool_file.file_key,
                    )

                    meta_copy = dict(getattr(message, "meta", {}) or {})
                    meta_copy.setdefault("tool_file_id", tool_file.id)
                    meta_copy.setdefault(
                        "tool_file_storage_key", tool_file.file_key)

                    yield ToolInvokeMessage(
                        type=ToolInvokeMessage.MessageType.IMAGE_LINK,
                        message=ToolInvokeMessage.TextMessage(text=url),
                        meta=meta_copy,
                    )
                except Exception as e:
                    meta_fallback = dict(getattr(message, "meta", {}) or {})
                    yield ToolInvokeMessage(
                        type=ToolInvokeMessage.MessageType.TEXT,
                        message=ToolInvokeMessage.TextMessage(
                            text=f"Failed to download image: {message.message.text}: {e}"
                        ),
                        meta=meta_fallback,
                    )
            elif message.type == ToolInvokeMessage.MessageType.BLOB:
                # get mime type and save blob to storage
                meta = dict(getattr(message, "meta", {}) or {})

                mimetype = meta.get("mime_type", "application/octet-stream")
                # get filename from meta
                filename = meta.get("filename")
                # if message is str, encode it to bytes

                if not isinstance(message.message, ToolInvokeMessage.BlobMessage):
                    raise ValueError("unexpected message type")

                assert isinstance(message.message.blob, bytes)
                tool_file_manager = ToolFileManager()
                tool_file = tool_file_manager.create_file_by_raw(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    file_binary=message.message.blob,
                    mimetype=mimetype,
                    filename=filename,
                )

                url = cls.get_tool_file_url(
                    tool_file_id=tool_file.id,
                    extension=guess_extension(tool_file.mimetype),
                    storage_key=tool_file.file_key,
                )

                meta_copy = dict(meta)
                meta_copy.setdefault("tool_file_id", tool_file.id)
                meta_copy.setdefault(
                    "tool_file_storage_key", tool_file.file_key)

                # check if file is image
                if "image" in mimetype:
                    yield ToolInvokeMessage(
                        type=ToolInvokeMessage.MessageType.IMAGE_LINK,
                        message=ToolInvokeMessage.TextMessage(text=url),
                        meta=meta_copy,
                    )
                else:
                    yield ToolInvokeMessage(
                        type=ToolInvokeMessage.MessageType.BINARY_LINK,
                        message=ToolInvokeMessage.TextMessage(text=url),
                        meta=meta_copy,
                        meta=meta_copy,
                    )
            elif message.type == ToolInvokeMessage.MessageType.FILE:
                meta = dict(getattr(message, "meta", {}) or {})
                file = meta.get("file", None)
                if isinstance(file, File):
                    if file.transfer_method == FileTransferMethod.TOOL_FILE:
                        assert file.related_id is not None
                        url = cls.get_tool_file_url(
                            tool_file_id=file.related_id,
                            extension=file.extension,
                            storage_key=file.storage_key,
                        )
                        meta_copy = dict(meta)
                        meta_copy.setdefault("tool_file_id", file.related_id)
                        meta_copy.setdefault(
                            "tool_file_storage_key", file.storage_key)
                        if file.type == FileType.IMAGE:
                            yield ToolInvokeMessage(
                                type=ToolInvokeMessage.MessageType.IMAGE_LINK,
                                message=ToolInvokeMessage.TextMessage(
                                    text=url),
                                meta=meta_copy,
                            )
                        else:
                            yield ToolInvokeMessage(
                                type=ToolInvokeMessage.MessageType.LINK,
                                message=ToolInvokeMessage.TextMessage(
                                    text=url),
                                meta=meta_copy,
                            )
                    else:
                        yield message

            elif message.type == ToolInvokeMessage.MessageType.JSON:
                if isinstance(message.message, ToolInvokeMessage.JsonMessage):
                    message.message.json_object = safe_json_value(
                        message.message.json_object)
                yield message
            else:
                yield message

    @classmethod
    def get_tool_file_url(cls, tool_file_id: str, extension: str | None, storage_key: str | None = None) -> str:
        return sign_tool_file(tool_file_id=tool_file_id, extension=extension or ".bin", storage_key=storage_key)

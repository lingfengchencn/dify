import logging
import os
from collections.abc import Generator
from pathlib import Path

import opendal
from dotenv import dotenv_values
from opendal import Operator

from extensions.storage.base_storage import BaseStorage

logger = logging.getLogger(__name__)


def _get_opendal_kwargs(*, scheme: str, env_file_path: str = ".env", prefix: str = "OPENDAL_"):
    kwargs = {}
    config_prefix = prefix + scheme.upper() + "_"
    for key, value in os.environ.items():
        if key.startswith(config_prefix):
            kwargs[key[len(config_prefix):].lower()] = value

    file_env_vars: dict = dotenv_values(env_file_path) or {}
    for key, value in file_env_vars.items():
        if key.startswith(config_prefix) and key[len(config_prefix):].lower() not in kwargs and value:
            kwargs[key[len(config_prefix):].lower()] = value

    return kwargs


class OpenDALStorage(BaseStorage):
    def __init__(self, scheme: str, **kwargs):
        kwargs = kwargs or _get_opendal_kwargs(scheme=scheme)
        self.scheme = scheme
        self._fs_root: Path | None = None

        if scheme == "fs":
            configured_root = kwargs.get("root", "storage")
            root_path = Path(configured_root).resolve()
            # Ensure the operator receives the effective root even when it isn't provided via kwargs/env
            kwargs.setdefault("root", str(root_path))
            root_path.mkdir(parents=True, exist_ok=True)
            self._fs_root = root_path

        retry_layer = opendal.layers.RetryLayer(
            max_times=3, factor=2.0, jitter=True)
        self.op = Operator(scheme=scheme, **kwargs).layer(retry_layer)
        logger.debug("opendal operator created with scheme %s", scheme)
        logger.debug("added retry layer to opendal operator")

    def save(self, filename: str, data: bytes):
        self.op.write(path=filename, bs=data)
        logger.debug("file %s saved", filename)

    def load_once(self, filename: str) -> bytes:
        if not self.exists(filename):
            raise FileNotFoundError("File not found")

        content: bytes = self.op.read(path=filename)
        logger.debug("file %s loaded", filename)
        return content

    def load_stream(self, filename: str) -> Generator:
        if not self.exists(filename):
            raise FileNotFoundError("File not found")

        batch_size = 4096
        with self.op.open(
            path=filename,
            mode="rb",
            chunck=batch_size,
        ) as file:
            while chunk := file.read(batch_size):
                yield chunk
        logger.debug("file %s loaded as stream", filename)

    def download(self, filename: str, target_filepath: str):
        if not self.exists(filename):
            raise FileNotFoundError("File not found")

        Path(target_filepath).write_bytes(self.op.read(path=filename))
        logger.debug("file %s downloaded to %s", filename, target_filepath)

    def exists(self, filename: str) -> bool:
        return self.op.exists(path=filename)

    def delete(self, filename: str):
        if self.exists(filename):
            self.op.delete(path=filename)
            logger.debug("file %s deleted", filename)
            return
        logger.debug("file %s not found, skip delete", filename)

    def scan(
        self,
        path: str,
        files: bool = True,
        directories: bool = False,
        recursive: bool = False,
    ) -> list[str]:
        if self.scheme == "fs" and self._fs_root is not None:
            return self._scan_local(path, files=files, directories=directories, recursive=recursive)

        if not self.exists(path):
            raise FileNotFoundError("Path not found")

        # Trim trailing slash so we don't keep re-visiting the same directory
        base_path = path.rstrip("/") if path else path

        items: list[str] = []

        if not recursive:
            entries = self.op.list(path=base_path)
            for entry in entries:
                entry_path = entry.path
                is_directory = entry_path.endswith("/")

                if is_directory:
                    if directories:
                        items.append(entry_path)
                else:
                    if files:
                        items.append(entry_path)

            logger.debug(
                "scanned %s on %s (recursive=%s)",
                "files/directories" if files and directories else "files" if files else "directories",
                path,
                recursive,
            )
            return items

        # Depth-first traversal for recursive listing
        stack: list[str] = [base_path]
        visited: set[str] = set()

        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)

            for entry in self.op.list(path=current):
                entry_path = entry.path
                is_directory = entry_path.endswith("/")

                if is_directory:
                    if directories:
                        items.append(entry_path)
                    stack.append(entry_path.rstrip("/"))
                else:
                    if files:
                        items.append(entry_path)

        logger.debug(
            "recursively scanned %d items under %s", len(items), path
        )
        return items

    def _scan_local(
        self,
        path: str,
        *,
        files: bool,
        directories: bool,
        recursive: bool,
    ) -> list[str]:
        if self._fs_root is None:
            raise FileNotFoundError("Local storage root not configured")

        root_path = self._fs_root
        target_path = (root_path / path).resolve() if path else root_path

        try:
            target_path.relative_to(root_path)
        except ValueError:
            # Requested path escapes the storage root
            raise FileNotFoundError("Path not found")

        if not target_path.exists():
            raise FileNotFoundError("Path not found")

        def _relative(entry: Path) -> str:
            return entry.relative_to(root_path).as_posix()

        if target_path.is_file():
            return [_relative(target_path)] if files else []

        items: list[str] = []
        iterator = target_path.rglob(
            "*") if recursive else target_path.iterdir()

        for entry in iterator:
            if entry.is_dir():
                if directories:
                    items.append(f"{_relative(entry).rstrip('/')}/")
                continue

            if files:
                items.append(_relative(entry))

        logger.debug(
            "locally scanned %d items under %s (recursive=%s)",
            len(items),
            path or ".",
            recursive,
        )
        return items

    def get_url(self, filename: str, *, expires_in: int) -> str:
        raise NotImplementedError(
            "OpenDAL filesystem storage does not expose public URLs")

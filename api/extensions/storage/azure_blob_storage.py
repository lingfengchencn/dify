from collections.abc import Generator
from datetime import timedelta

from azure.identity import ChainedTokenCredential, DefaultAzureCredential
from azure.storage.blob import (
    AccountSasPermissions,
    BlobSasPermissions,
    BlobServiceClient,
    ResourceTypes,
    generate_account_sas,
    generate_blob_sas,
)

from configs import dify_config
from extensions.ext_redis import redis_client
from extensions.storage.base_storage import BaseStorage
from libs.datetime_utils import naive_utc_now


class AzureBlobStorage(BaseStorage):
    """Implementation for Azure Blob storage."""

    def __init__(self):
        super().__init__()
        self.bucket_name = dify_config.AZURE_BLOB_CONTAINER_NAME
        self.account_url = dify_config.AZURE_BLOB_ACCOUNT_URL
        self.account_name = dify_config.AZURE_BLOB_ACCOUNT_NAME
        self.account_key = dify_config.AZURE_BLOB_ACCOUNT_KEY

        self.credential: ChainedTokenCredential | None = None
        if self.account_key == "managedidentity":
            self.credential = DefaultAzureCredential()
        else:
            self.credential = None

    def save(self, filename, data):
        if not self.bucket_name:
            return

        client = self._sync_client()
        blob_container = client.get_container_client(container=self.bucket_name)
        blob_container.upload_blob(filename, data)

    def load_once(self, filename: str) -> bytes:
        if not self.bucket_name:
            raise FileNotFoundError("Azure bucket name is not configured.")

        client = self._sync_client()
        blob = client.get_container_client(container=self.bucket_name)
        blob = blob.get_blob_client(blob=filename)
        data = blob.download_blob().readall()
        if not isinstance(data, bytes):
            raise TypeError(f"Expected bytes from blob.readall(), got {type(data).__name__}")
        return data

    def load_stream(self, filename: str) -> Generator:
        if not self.bucket_name:
            raise FileNotFoundError("Azure bucket name is not configured.")

        client = self._sync_client()
        blob = client.get_blob_client(container=self.bucket_name, blob=filename)
        blob_data = blob.download_blob()
        yield from blob_data.chunks()

    def download(self, filename, target_filepath):
        if not self.bucket_name:
            return

        client = self._sync_client()

        blob = client.get_blob_client(container=self.bucket_name, blob=filename)
        with open(target_filepath, "wb") as my_blob:
            blob_data = blob.download_blob()
            blob_data.readinto(my_blob)

    def exists(self, filename):
        if not self.bucket_name:
            return False

        client = self._sync_client()

        blob = client.get_blob_client(container=self.bucket_name, blob=filename)
        return blob.exists()

    def delete(self, filename):
        if not self.bucket_name:
            return

        client = self._sync_client()

        blob_container = client.get_container_client(container=self.bucket_name)
        blob_container.delete_blob(filename)

    def get_url(self, filename: str, *, expires_in: int) -> str:
        blob_path = filename.lstrip("/")
        if dify_config.AZURE_BLOB_PUBLIC_BASE_URL:
            base = dify_config.AZURE_BLOB_PUBLIC_BASE_URL.rstrip("/")
            return f"{base}/{blob_path}"

        if not self.bucket_name or not self.account_url:
            raise NotImplementedError("Azure Blob public URL is not configured; set AZURE_BLOB_PUBLIC_BASE_URL")

        if not self.account_key or self.account_key == "managedidentity":
            raise NotImplementedError(
                "Azure Blob signing requires account key; set AZURE_BLOB_PUBLIC_BASE_URL or account key"
            )

        sas_token = generate_blob_sas(
            account_name=self.account_name or "",
            account_key=self.account_key,
            container_name=self.bucket_name,
            blob_name=blob_path,
            permission=BlobSasPermissions(read=True),
            expiry=naive_utc_now() + timedelta(seconds=expires_in),
        )
        base = self.account_url.rstrip("/")
        return f"{base}/{self.bucket_name}/{blob_path}?{sas_token}"

    def _sync_client(self):
        if self.account_key == "managedidentity":
            return BlobServiceClient(account_url=self.account_url, credential=self.credential)  # type: ignore

        cache_key = f"azure_blob_sas_token_{self.account_name}_{self.account_key}"
        cache_result = redis_client.get(cache_key)
        if cache_result is not None:
            sas_token = cache_result.decode("utf-8")
        else:
            sas_token = generate_account_sas(
                account_name=self.account_name or "",
                account_key=self.account_key or "",
                resource_types=ResourceTypes(service=True, container=True, object=True),
                permission=AccountSasPermissions(read=True, write=True, delete=True, list=True, add=True, create=True),
                expiry=naive_utc_now() + timedelta(hours=1),
            )
            redis_client.set(cache_key, sas_token, ex=3000)
        return BlobServiceClient(account_url=self.account_url or "", credential=sas_token)

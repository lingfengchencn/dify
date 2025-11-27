from pydantic import Field
from pydantic_settings import BaseSettings


class GoogleCloudStorageConfig(BaseSettings):
    """
    Configuration settings for Google Cloud Storage
    """

    GOOGLE_STORAGE_BUCKET_NAME: str | None = Field(
        description="Name of the Google Cloud Storage bucket to store and retrieve objects (e.g., 'my-gcs-bucket')",
        default=None,
    )

    GOOGLE_STORAGE_SERVICE_ACCOUNT_JSON_BASE64: str | None = Field(
        description="Base64-encoded JSON key file for Google Cloud service account authentication",
        default=None,
    )

    GOOGLE_STORAGE_PUBLIC_BASE_URL: str | None = Field(
        description="Public base URL for serving Google Cloud Storage objects",
        default=None,
    )

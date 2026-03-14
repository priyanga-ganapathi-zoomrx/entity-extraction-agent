"""Storage protocol and implementations for file operations.

Provides a unified interface for storage operations that can work with
local filesystem or cloud storage (GCS).

Usage:
    # Local storage
    storage = get_storage_client("data/ASCO2025/drug")
    
    # GCS storage
    storage = get_storage_client("gs://bucket-name/ASCO2025/drug")
    
    # Then use the same interface
    storage.upload_json("abstracts/123/extraction.json", data)
    data = storage.download_json("abstracts/123/extraction.json")
"""

import json
from pathlib import Path
from typing import Protocol, Union

from google.cloud import storage as gcs_storage
from google.cloud.exceptions import NotFound

from src.agents.core.ems_logger import get_logger

ems_logger = get_logger("storage")


class StorageClient(Protocol):
    """Protocol for storage operations (GCS or local filesystem)."""
    
    def download_json(self, path: str) -> dict:
        """Download JSON from storage."""
        ...
    
    def upload_json(self, path: str, data: dict) -> None:
        """Upload JSON to storage."""
        ...
    
    def download_text(self, path: str) -> str:
        """Download text content from storage."""
        ...
    
    def upload_text(self, path: str, content: str) -> None:
        """Upload text content to storage."""
        ...
    
    def upload_bytes(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        """Upload binary data to storage."""
        ...

    def exists(self, path: str) -> bool:
        """Check if path exists in storage."""
        ...


class LocalStorageClient:
    """Local filesystem storage client for development/testing."""
    
    def __init__(self, base_dir: str = "output"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_path(self, path: str) -> Path:
        return self.base_dir / path
    
    def download_json(self, path: str) -> dict:
        """Download JSON from local filesystem."""
        file_path = self._get_path(path)
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def upload_json(self, path: str, data: dict) -> None:
        """Upload JSON to local filesystem."""
        file_path = self._get_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def download_text(self, path: str) -> str:
        """Download text content from local filesystem."""
        file_path = self._get_path(path)
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            return f.read()
    
    def upload_text(self, path: str, content: str) -> None:
        """Upload text content to local filesystem."""
        file_path = self._get_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8-sig', newline='') as f:
            f.write(content)
    
    def upload_bytes(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        """Upload binary data to local filesystem."""
        file_path = self._get_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'wb') as f:
            f.write(data)

    def exists(self, path: str) -> bool:
        """Check if path exists in local filesystem."""
        return self._get_path(path).exists()


class GCSStorageClient:
    """Google Cloud Storage client for production.
    
    Uses GOOGLE_APPLICATION_CREDENTIALS environment variable for authentication.
    Falls back to application default credentials if not set.
    Project ID can be set via GCS_PROJECT_ID or GOOGLE_CLOUD_PROJECT env vars,
    or via the settings object from config.py.
    """
    
    def __init__(self, bucket_name: str, base_prefix: str = ""):
        """Initialize GCS client.
        
        Args:
            bucket_name: Name of the GCS bucket
            base_prefix: Optional prefix path within the bucket
        """
        import os
        from src.agents.core.config import settings
        
        # Set credentials from settings to os.environ (Google SDK reads from os.environ)
        if settings.gcs.GOOGLE_APPLICATION_CREDENTIALS:
            creds_path = settings.gcs.GOOGLE_APPLICATION_CREDENTIALS.strip()
            if creds_path and os.path.exists(creds_path):
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
        
        # Get project ID from settings, or read from credentials file
        project_id = settings.gcs.GCS_PROJECT_ID.strip() if settings.gcs.GCS_PROJECT_ID else None
        
        if not project_id:
            creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
            if creds_path and os.path.exists(creds_path):
                try:
                    with open(creds_path) as f:
                        project_id = json.load(f).get("project_id")
                except Exception as e:
                    ems_logger.error(
                        "gcs_credentials_read_failed",
                        creds_path=creds_path,
                        error=str(e),
                        exc_info=True,
                    )
        
        # Initialize client
        self.client = gcs_storage.Client(project=project_id) if project_id else gcs_storage.Client()
        
        self.bucket = self.client.bucket(bucket_name)
        self.base_prefix = base_prefix.strip("/") if base_prefix else ""
    
    def _get_blob_path(self, path: str) -> str:
        """Get full blob path including base prefix."""
        if self.base_prefix:
            return f"{self.base_prefix}/{path}"
        return path
    
    def download_json(self, path: str) -> dict:
        """Download JSON from GCS."""
        blob = self.bucket.blob(self._get_blob_path(path))
        try:
            content = blob.download_as_text()
            return json.loads(content)
        except NotFound:
            raise FileNotFoundError(f"GCS object not found: {path}")
    
    def upload_json(self, path: str, data: dict) -> None:
        """Upload JSON to GCS."""
        blob = self.bucket.blob(self._get_blob_path(path))
        content = json.dumps(data, indent=2, ensure_ascii=False)
        blob.upload_from_string(content, content_type="application/json")
    
    def download_text(self, path: str) -> str:
        """Download text content from GCS."""
        blob = self.bucket.blob(self._get_blob_path(path))
        try:
            content = blob.download_as_text()
            # Strip UTF-8 BOM if present (for consistency with LocalStorageClient which uses utf-8-sig)
            return content.lstrip('\ufeff')
        except NotFound:
            raise FileNotFoundError(f"GCS object not found: {path}")
    
    def upload_text(self, path: str, content: str) -> None:
        """Upload text content to GCS."""
        blob = self.bucket.blob(self._get_blob_path(path))
        blob.upload_from_string(content, content_type="text/plain; charset=utf-8")
    
    def upload_bytes(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        """Upload binary data to GCS."""
        blob = self.bucket.blob(self._get_blob_path(path))
        blob.upload_from_string(data, content_type=content_type)

    def exists(self, path: str) -> bool:
        """Check if object exists in GCS."""
        blob = self.bucket.blob(self._get_blob_path(path))
        return blob.exists()


def parse_gcs_path(gcs_path: str) -> tuple[str, str]:
    """Parse a GCS path into bucket name and prefix.
    
    Args:
        gcs_path: Path in format "gs://bucket-name/optional/prefix"
        
    Returns:
        tuple: (bucket_name, prefix)
        
    Raises:
        ValueError: If path doesn't start with "gs://"
    """
    if not gcs_path.startswith("gs://"):
        raise ValueError(f"Invalid GCS path: {gcs_path}. Must start with 'gs://'")
    
    # Remove "gs://" prefix
    path = gcs_path[5:]
    
    # Split into bucket and prefix
    parts = path.split("/", 1)
    bucket_name = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    
    return bucket_name, prefix


def get_storage_client(base_path: str) -> Union[GCSStorageClient, LocalStorageClient]:
    """Create appropriate storage client based on path.
    
    Automatically detects whether to use GCS or local storage based on path prefix.
    
    Args:
        base_path: Either "gs://bucket-name/prefix" for GCS or local path for filesystem
        
    Returns:
        GCSStorageClient for gs:// paths, LocalStorageClient otherwise
        
    Examples:
        # GCS storage
        storage = get_storage_client("gs://my-bucket/ASCO2025/drug")
        
        # Local storage
        storage = get_storage_client("data/ASCO2025/drug")
    """
    if base_path.startswith("gs://"):
        bucket_name, prefix = parse_gcs_path(base_path)
        return GCSStorageClient(bucket_name, prefix)
    
    return LocalStorageClient(base_path)


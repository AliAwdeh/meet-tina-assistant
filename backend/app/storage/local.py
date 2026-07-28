import hashlib
import re
import uuid

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.entities import File

SAFE_EXTENSIONS = {
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
}


def normalize_mime_type(mime_type: str) -> str:
    return mime_type.split(";", 1)[0].strip().lower()


def sanitize_name(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip()
    return cleaned[:180] or None


class LocalStorage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.data_dir

    def category_for_mime(self, mime_type: str) -> str:
        if mime_type.startswith("audio/"):
            return "media/audio"
        if mime_type.startswith("image/"):
            return "media/images"
        return "media/documents"

    def save_bytes(
        self,
        db: Session,
        *,
        content: bytes,
        mime_type: str,
        source: str,
        original_file_name: str | None = None,
    ) -> File:
        if len(content) > self.settings.media_max_bytes:
            raise ValueError("media file exceeds configured size limit")
        mime_type = normalize_mime_type(mime_type)
        extension = SAFE_EXTENSIONS.get(mime_type, ".bin")
        safe_name = f"{uuid.uuid4()}{extension}"
        relative_path = f"{self.category_for_mime(mime_type)}/{safe_name}"
        target = (self.root / relative_path).resolve()
        root = self.root.resolve()
        if not str(target).startswith(str(root)):
            raise ValueError("invalid storage target")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        record = File(
            original_file_name=sanitize_name(original_file_name),
            safe_file_name=safe_name,
            relative_path=relative_path,
            mime_type=mime_type,
            checksum=hashlib.sha256(content).hexdigest(),
            file_size=len(content),
            source=source,
        )
        db.add(record)
        db.flush()
        return record

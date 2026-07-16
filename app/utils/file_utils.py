from pathlib import Path
from uuid import uuid4
import hashlib

from fastapi import HTTPException, UploadFile, status

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".csv"}


def validate_upload_file(file: UploadFile) -> str:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type. Allowed: {allowed}",
        )
    return suffix.lstrip(".")


def safe_filename(filename: str) -> str:
    original = Path(filename).name
    return f"{uuid4().hex}_{original}"


async def save_upload_file(file: UploadFile, upload_dir: Path, max_size_bytes: int) -> Path:
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / safe_filename(file.filename or "uploaded_file")
    size = 0

    try:
        with target.open("wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_size_bytes:
                    target.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="Uploaded file is too large")
                buffer.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Failed to save uploaded file") from exc
    finally:
        await file.close()

    return target


def calculate_file_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as buffer:
        for chunk in iter(lambda: buffer.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

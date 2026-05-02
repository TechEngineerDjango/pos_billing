"""
S6: Reusable image upload validation utility.

Validates content type, file size, and image integrity for all upload endpoints.
"""
import io
from fastapi import HTTPException, UploadFile
from PIL import Image

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


async def validate_image_upload(file: UploadFile) -> bytes:
    """
    Validate an uploaded file is a real image within size limits.

    Returns the raw bytes on success; raises HTTPException on failure.
    """
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid image type '{file.content_type}'. Allowed: {', '.join(ALLOWED_CONTENT_TYPES)}",
        )

    data = await file.read()

    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({len(data)} bytes). Maximum is {MAX_FILE_SIZE // (1024 * 1024)} MB.",
        )

    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
    except Exception:
        raise HTTPException(status_code=400, detail="Corrupt or invalid image file.")

    return data

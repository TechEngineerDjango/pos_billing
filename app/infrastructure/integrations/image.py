"""
Reusable image upload service.

Centralizes file validation, storage, and resizing to prevent:
- Unrestricted file type uploads (S3)
- Path traversal attacks
- Code duplication across routers (P3)
"""
import os
import uuid
import logging
from fastapi import UploadFile, HTTPException
from PIL import Image

logger = logging.getLogger(__name__)

# Whitelist of allowed image extensions
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
UPLOAD_DIR = "app/frontend/static/uploads"


def _validate_extension(filename: str) -> str:
    """Extract and validate file extension against whitelist."""
    if not filename or "." not in filename:
        raise HTTPException(status_code=400, detail="Invalid filename — missing extension")
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '.{ext}' not allowed. Accepted: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    return ext


async def save_uploaded_image(
    file: UploadFile,
    max_size: tuple[int, int] = (300, 300),
    prefix: str = "img",
) -> str:
    """
    Validate, save, and resize an uploaded image.

    Returns the URL path (e.g., '/static/uploads/img_<uuid>.jpg').
    Raises HTTPException on validation failure.
    """
    if not file or not file.filename:
        return None

    ext = _validate_extension(file.filename)
    filename = f"{prefix}_{uuid.uuid4()}.{ext}"
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    filepath = os.path.join(UPLOAD_DIR, filename)

    # Read file content with size check
    content = await file.read()
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE_BYTES // (1024*1024)} MB"
        )

    try:
        with open(filepath, "wb") as buffer:
            buffer.write(content)

        # Validate it's actually an image and resize
        with Image.open(filepath) as img:
            img.verify()  # Verify it's a valid image

        # Re-open after verify (verify() invalidates the file pointer)
        with Image.open(filepath) as img:
            img.thumbnail(max_size)
            img.save(filepath)

        return f"/static/uploads/{filename}"

    except (OSError, IOError, SyntaxError) as e:
        # Clean up failed upload
        if os.path.exists(filepath):
            os.remove(filepath)
        logger.warning(f"Image upload failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid or corrupted image file")

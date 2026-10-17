from __future__ import annotations

import uuid
from pathlib import Path
from typing import BinaryIO

from flask import current_app
from PIL import Image, UnidentifiedImageError


class InvalidImageError(ValueError):
    pass


def save_uploaded_image(file_storage: BinaryIO) -> str:
    max_size = current_app.config["MAX_IMAGE_SIZE"]
    raw = file_storage.read(max_size + 1)
    file_storage.seek(0)
    if len(raw) > max_size:
        raise InvalidImageError("Cada imagen debe pesar como máximo 8 MB.")
    if not raw:
        raise InvalidImageError("El archivo de imagen está vacío.")

    try:
        image = Image.open(file_storage.stream)
        if image.format not in {"JPEG", "PNG", "WEBP"}:
            raise InvalidImageError("Sube una imagen JPEG, PNG o WebP válida.")
        image.verify()
        file_storage.stream.seek(0)
        image = Image.open(file_storage.stream)
        if image.width * image.height > current_app.config["MAX_IMAGE_PIXELS"]:
            raise InvalidImageError("La resolución de la imagen es demasiado grande.")
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        filename = f"{uuid.uuid4().hex}.webp"
        destination = Path(current_app.config["UPLOAD_FOLDER"]) / filename
        image.save(destination, format="WEBP", quality=86, method=5)
        return filename
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise InvalidImageError("Sube una imagen JPEG, PNG o WebP válida.") from error

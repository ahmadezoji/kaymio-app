"""Small Pillow helpers for preparing images for social platforms."""
from __future__ import annotations

from io import BytesIO
from typing import Tuple

from PIL import Image, ImageOps

WHITE = (255, 255, 255)


def pad_to_canvas(
    image_bytes: bytes,
    size: Tuple[int, int],
    *,
    background: Tuple[int, int, int] = WHITE,
    jpeg_quality: int = 92,
) -> bytes:
    """Fit the whole image inside ``size`` on a solid canvas and return JPEG bytes.

    Unlike a center crop, nothing of the product is cut off, which matters for
    marketplace photos that are often very tall or very wide. Instagram's
    content publishing API only accepts JPEG, hence the fixed output format.
    """
    with Image.open(BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source)
        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            rgba = image.convert("RGBA")
            flattened = Image.new("RGB", rgba.size, background)
            flattened.paste(rgba, mask=rgba.split()[-1])
            image = flattened
        else:
            image = image.convert("RGB")

        fitted = ImageOps.contain(image, size, method=Image.LANCZOS)
        canvas = Image.new("RGB", size, background)
        offset = ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2)
        canvas.paste(fitted, offset)

    output = BytesIO()
    canvas.save(output, format="JPEG", quality=jpeg_quality, optimize=True)
    return output.getvalue()

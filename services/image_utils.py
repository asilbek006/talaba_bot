from io import BytesIO

from PIL import Image, ImageOps

MAX_INPUT_IMAGE_BYTES = 12 * 1024 * 1024
MAX_OUTPUT_IMAGE_BYTES = 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000
MAX_IMAGE_SIDE = 8_000


def normalize_ppt_image(image_bytes: bytes) -> bytes:
    """Validate and re-encode an image to a small, safe JPEG."""
    if not isinstance(image_bytes, (bytes, bytearray)):
        raise ValueError("Image response is not binary data")
    if not image_bytes or len(image_bytes) > MAX_INPUT_IMAGE_BYTES:
        raise ValueError("Image response exceeds the input size limit")

    source = bytes(image_bytes)
    with Image.open(BytesIO(source)) as image:
        if image.format not in {"JPEG", "PNG", "WEBP"}:
            raise ValueError("Unsupported image format")
        width, height = image.size
        if (width < 1 or height < 1 or width > MAX_IMAGE_SIDE
                or height > MAX_IMAGE_SIDE or width * height > MAX_IMAGE_PIXELS):
            raise ValueError("Image dimensions exceed the limit")
        image.verify()

    with Image.open(BytesIO(source)) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode in {"RGBA", "LA"}:
            rgba = image.convert("RGBA")
            rgb = Image.new("RGB", rgba.size, "white")
            rgb.paste(rgba, mask=rgba.getchannel("A"))
            image = rgb
        else:
            image = image.convert("RGB")

        resampling = getattr(Image, "Resampling", Image).LANCZOS
        image.thumbnail((1600, 900), resampling)
        output = BytesIO()
        image.save(output, format="JPEG", quality=88, optimize=True)
        if output.tell() > MAX_OUTPUT_IMAGE_BYTES:
            image.thumbnail((1280, 720), resampling)
            output = BytesIO()
            image.save(output, format="JPEG", quality=74, optimize=True)
        if output.tell() > MAX_OUTPUT_IMAGE_BYTES:
            raise ValueError("Normalized image exceeds the output size limit")
        return output.getvalue()

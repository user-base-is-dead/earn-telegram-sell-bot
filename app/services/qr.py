"""Render arbitrary data (e.g. a crypto deposit address) as a QR code image."""
from io import BytesIO

import qrcode


def generate_qr_png(data: str) -> BytesIO:
    """Render `data` as a PNG QR code and return an in-memory buffer."""
    qr = qrcode.QRCode(
        version=None,  # auto-size to fit the data
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    buf.name = "payment_qr.png"
    return buf

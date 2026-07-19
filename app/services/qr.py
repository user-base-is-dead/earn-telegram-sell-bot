"""Build UPI payment links and render them as QR code images."""
from io import BytesIO
from urllib.parse import quote

import qrcode

from app import config


def build_upi_uri(amount: float, note: str) -> str:
    """Construct a standard UPI deep link.

    Format: upi://pay?pa=<vpa>&pn=<name>&am=<amount>&cu=INR&tn=<note>
    Any UPI app (GPay, PhonePe, Paytm, ...) can scan and pre-fill this.
    UPI is INR-only, so the currency is always INR.
    """
    params = (
        f"pa={quote(config.UPI_ID)}"
        f"&pn={quote(config.UPI_PAYEE_NAME)}"
        f"&am={amount:.2f}"
        f"&cu=INR"
        f"&tn={quote(note)}"
    )
    return f"upi://pay?{params}"


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


def generate_payment_qr(amount: float, note: str) -> tuple[BytesIO, str]:
    """Return (qr_png_buffer, upi_uri) for a payment of `amount`."""
    uri = build_upi_uri(amount, note)
    return generate_qr_png(uri), uri

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if getattr(root, "_bill_ocr_configured", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    root.addHandler(handler)
    root.setLevel(level.upper())
    # Third-party noise
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("multipart").setLevel(logging.WARNING)
    root._bill_ocr_configured = True  # type: ignore[attr-defined]

"""Domain exceptions. Each carries the HTTP status the API should return."""


class BillOCRError(Exception):
    status_code = 500
    error_code = "internal_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    @property
    def extra(self) -> dict:
        """Additional fields for the JSON error body."""
        return {}


class InvalidUploadError(BillOCRError):
    status_code = 400
    error_code = "invalid_upload"


class EmptyFileError(InvalidUploadError):
    status_code = 400
    error_code = "empty_file"


class UnsupportedFileTypeError(InvalidUploadError):
    status_code = 415
    error_code = "unsupported_file_type"


class FileTooLargeError(InvalidUploadError):
    status_code = 413
    error_code = "file_too_large"


class DuplicateFileError(BillOCRError):
    """The exact same file (byte for byte) is already stored as a bill."""

    status_code = 409
    error_code = "duplicate_file"

    def __init__(self, message: str, existing_bill_id: int):
        super().__init__(message)
        self.existing_bill_id = existing_bill_id

    @property
    def extra(self) -> dict:
        return {"existing_bill_id": self.existing_bill_id}


class DocumentProcessingError(BillOCRError):
    """Corrupt / unreadable PDF or image."""

    status_code = 422
    error_code = "document_processing_failed"


class OCRUnavailableError(BillOCRError):
    """Tesseract binary is missing or misconfigured."""

    status_code = 503
    error_code = "ocr_unavailable"


class OCRProcessingError(BillOCRError):
    status_code = 500
    error_code = "ocr_failed"


class DatabaseUnavailableError(BillOCRError):
    status_code = 503
    error_code = "database_unavailable"


class BillNotFoundError(BillOCRError):
    status_code = 404
    error_code = "bill_not_found"


class FileNotAvailableError(BillOCRError):
    """The bill exists but its original upload is gone (e.g. a host with a temporary disk)."""

    status_code = 404
    error_code = "file_not_available"


class PageNotFoundError(BillOCRError):
    status_code = 404
    error_code = "page_not_found"

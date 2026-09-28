"""Domain exceptions. Each carries the HTTP status the API should return."""


class BillOCRError(Exception):
    status_code = 500
    error_code = "internal_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


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

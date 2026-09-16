class APIError(Exception):
    def __init__(self, status, code, message, field_errors=None, retryable=False):
        self.status = status
        self.code = code
        self.message = message
        self.field_errors = field_errors or {}
        self.retryable = retryable
        super().__init__(message)

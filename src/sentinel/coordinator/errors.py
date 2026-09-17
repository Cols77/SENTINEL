"""Stable coordinator command rejection errors."""


class CoordinatorError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


class InvalidContract(CoordinatorError):
    def __init__(self, message: str):
        super().__init__("INVALID_CONTRACT", message)

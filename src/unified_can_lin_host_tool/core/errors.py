from enum import Enum


class ErrorCategory(str, Enum):
    DEVICE = "device"
    PROFILE = "profile"
    FILE = "file"
    TRANSPORT = "transport"
    UDS = "uds"
    FLASH_STATE = "flash_state"


class HostToolError(Exception):
    def __init__(self, category: ErrorCategory, message: str):
        super().__init__(f"{category.value}: {message}")
        self.category = category
        self.message = message


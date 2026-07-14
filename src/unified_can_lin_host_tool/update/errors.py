"""自动更新调用方可稳定判断的错误类型。"""


class UpdateError(RuntimeError):
    code = "UPDATE_FAILED"


class UpdateMetadataError(UpdateError):
    code = "UPDATE_METADATA_INVALID"


class UpdateSecurityError(UpdateError):
    code = "UPDATE_SIGNATURE_INVALID"


class UpdateNetworkError(UpdateError):
    code = "UPDATE_NETWORK_UNAVAILABLE"


class UpdateIntegrityError(UpdateError):
    code = "UPDATE_INSTALLER_INTEGRITY_FAILED"


class UpdateBusyError(UpdateError):
    code = "UPDATE_TOOL_BUSY"


class UpdateInstallerError(UpdateError):
    code = "UPDATE_INSTALLER_START_FAILED"

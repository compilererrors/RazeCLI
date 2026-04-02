"""Domain exceptions for RazeCLI."""


class RazeCliError(Exception):
    """Base class for expected CLI errors."""


class BackendUnavailableError(RazeCliError):
    """Raised when backend cannot be initialized."""


class CapabilityUnsupportedError(RazeCliError):
    """Raised when a backend/device does not support a requested capability."""


class DeviceSelectionError(RazeCliError):
    """Raised when we cannot resolve a unique target device."""

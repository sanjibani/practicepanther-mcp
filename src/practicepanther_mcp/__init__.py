"""PracticePanther MCP — public surface."""
from .client import (
    PracticePantherAPIError,
    PracticePantherAuthError,
    PracticePantherClient,
)
from .server import main, mcp

__version__ = "0.1.0"
__all__ = [
    "PracticePantherAPIError",
    "PracticePantherAuthError",
    "PracticePantherClient",
    "main",
    "mcp",
]

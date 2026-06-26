"""PracticePanther MCP — MCP server."""
from .client import PracticepantherAPIError, PracticepantherAuthError, PracticepantherClient
from .server import main, mcp

__version__ = "0.1.0"
__all__ = [
    "PracticepantherAPIError",
    "PracticepantherAuthError",
    "PracticepantherClient",
    "main",
    "mcp",
]
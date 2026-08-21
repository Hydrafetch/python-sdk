"""Official Python client for the Hydrafetch web data API."""

from ._client import AsyncHydrafetch, Hydrafetch
from .errors import HydrafetchError, HydrafetchTimeout

__all__ = ["AsyncHydrafetch", "Hydrafetch", "HydrafetchError", "HydrafetchTimeout"]
__version__ = "0.1.0"

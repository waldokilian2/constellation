"""
Framework detection registry.

:data:`HANDLERS` is the ordered list of framework handlers the entry detector
runs per class. Order matters only for entry-point ordering within a class
(handlers are independent). **Adding a framework = append a handler here.**
"""
from .base import FrameworkHandler, ScanContext
from .spring import SpringHandler
from .jakarta import JakartaHandler
from .extra import ExtraHandler
from .camel import CamelHandler

# Ordered: Spring, Jakarta, then the extra-framework tier, then Camel.
HANDLERS: list[FrameworkHandler] = [
    SpringHandler(),
    JakartaHandler(),
    ExtraHandler(),
    CamelHandler(),
]

__all__ = ["FrameworkHandler", "ScanContext", "HANDLERS", "SpringHandler", "JakartaHandler", "ExtraHandler", "CamelHandler"]

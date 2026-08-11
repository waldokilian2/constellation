"""
Aggregator for all language specs.

Each ``specs/<tag>.py`` module exports a single ``SPEC``. This module collects
them into ``ALL_SPECS`` (order-significant: the registry builds its
extension→tag map first-spec-wins). **Adding a language = add a module here +
import it below.**
"""
from .java import SPEC as _java

ALL_SPECS: tuple = (
    _java,
)

__all__ = ["ALL_SPECS"]

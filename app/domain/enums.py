from __future__ import annotations

from enum import Enum


class Currency(str, Enum):
    """Supported currencies."""

    CLP = "CLP"

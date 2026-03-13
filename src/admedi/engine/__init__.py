"""Admedi config engine.

Re-exports engine components for convenient access::

    from admedi.engine import ConfigEngine, Applier, compute_diff, load_template, generate_snapshot
"""

from admedi.engine.applier import Applier
from admedi.engine.differ import compute_diff
from admedi.engine.engine import ConfigEngine
from admedi.engine.loader import load_template
from admedi.engine.snapshot import generate_snapshot

__all__ = [
    "Applier",
    "ConfigEngine",
    "compute_diff",
    "generate_snapshot",
    "load_template",
]

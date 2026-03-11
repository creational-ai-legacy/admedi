"""Admedi adapter interfaces.

Re-exports the abstract base classes and capability enum for convenient access::

    from admedi.adapters import MediationAdapter, StorageAdapter, AdapterCapability
"""

from admedi.adapters.mediation import AdapterCapability, MediationAdapter
from admedi.adapters.storage import StorageAdapter

__all__ = [
    "AdapterCapability",
    "MediationAdapter",
    "StorageAdapter",
]

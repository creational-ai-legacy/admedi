"""Admedi storage adapters.

Re-exports the concrete storage adapter implementations::

    from admedi.storage import LocalFileStorageAdapter
"""

from admedi.storage.local import LocalFileStorageAdapter

__all__ = [
    "LocalFileStorageAdapter",
]

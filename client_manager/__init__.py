"""Compatibility package: provide `client_manager` package namespace
that proxies to the existing `app` package for tests and imports.
"""

__all__ = ["app"]

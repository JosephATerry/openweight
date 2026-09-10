"""Production-style HTTP boundary for the OpenWeight platform.

Import :mod:`openweight_platform.api.app` explicitly when constructing the
service.  Keeping the package initializer empty avoids creating a global
FastAPI application as a side effect of importing configuration or contracts.
"""

__all__: list[str] = []

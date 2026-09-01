"""Root pytest conftest.

Ensures the project root is on ``sys.path`` (pytest prepend import mode),
so tests can import package modules (e.g. ``from src.detector...``)
regardless of how pytest is invoked.
"""

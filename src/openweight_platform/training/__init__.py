"""Experimental training compatibility helpers.

The concrete Muse wrapper is intentionally not imported here. The normal
runtime environment uses an older Transformers release, while training uses
the isolated ``.venv-train`` environment declared by
``requirements-training.txt``.
"""

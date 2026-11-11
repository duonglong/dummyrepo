"""Attachment management: one search, preview, zip download, naming and duplicate warning.

Requirements: FR-AM-0001 (the module extends the native attachment model).
"""
from . import controllers
from . import models
from .hooks import post_init_hook, pre_init_hook

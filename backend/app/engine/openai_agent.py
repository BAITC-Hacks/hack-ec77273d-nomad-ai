"""Compatibility import; all providers share the validated ai_layer adapter.

There is no second SDK runner, tracing uploader, timeout or model protocol here.
"""

from .ai_layer import rank_and_explain

__all__ = ["rank_and_explain"]

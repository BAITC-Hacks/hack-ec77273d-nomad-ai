"""Uvicorn entry point; implementation lives in application.py."""
from .application import app, create_app

__all__ = ['app', 'create_app']

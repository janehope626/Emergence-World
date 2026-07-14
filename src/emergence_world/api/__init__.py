# 暴露 FastAPI 应用工厂。
"""FastAPI observability service."""

from emergence_world.api.app import create_app

__all__ = ["create_app"]

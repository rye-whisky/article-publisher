from .status import router as status_router
from .articles import router as articles_router
from .pipeline import router as pipeline_router
from .logs import router as logs_router
from .scheduler import router as scheduler_router
from .memory import memory_router
from .auth import router as auth_router
from .database import router as database_router
from .settings import router as settings_router

__all__ = ["status_router", "articles_router", "pipeline_router", "logs_router", "scheduler_router", "memory_router", "auth_router", "database_router", "settings_router"]

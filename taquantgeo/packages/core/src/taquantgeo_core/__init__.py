"""taquantgeo_core — shared models, config, and DB session."""

from taquantgeo_core.config import Settings, settings
from taquantgeo_core.db import make_engine, session_scope
from taquantgeo_core.schemas import Base, Vessel

__version__ = "0.0.1"
__all__ = [
    "Base",
    "Settings",
    "Vessel",
    "make_engine",
    "session_scope",
    "settings",
]

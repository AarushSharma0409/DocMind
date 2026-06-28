"""
limiter.py — shared rate limiter instance

Exists as a separate module to avoid a circular import:
  main.py imports routers → routers imported limiter from main.py
  → main.py not yet fully initialized → ImportError

Moving the limiter here breaks the cycle:
  main.py imports from limiter.py (no cycle)
  routers import from limiter.py (no cycle)
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=[])
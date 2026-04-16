"""
Entry point for the FastAPI server.
Run with: uvicorn main:app --reload
"""

import os
import sys

from dotenv import load_dotenv


sys.path.insert(0, os.path.dirname(__file__))
load_dotenv()

if not os.getenv("ANTHROPIC_API_KEY"):
    print("[startup] ANTHROPIC_API_KEY not set. Live Claude evaluation will fall back to deterministic/heuristic behavior.")

from api.routes import app  # noqa: E402,F401

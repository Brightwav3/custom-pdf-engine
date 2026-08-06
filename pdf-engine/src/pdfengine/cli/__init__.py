"""Command-line interfaces for people, scripts, and AI agents."""

from .agent import dispatch_json_line, run_agent
from .main import main

__all__ = ["dispatch_json_line", "main", "run_agent"]

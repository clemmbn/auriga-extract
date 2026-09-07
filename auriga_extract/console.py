"""
Shared rich Console instance.

Every module that prints to the terminal imports this instance instead of
using bare print(), so all output goes through one stream with consistent
styling.
"""

from __future__ import annotations

from rich.console import Console

console = Console()

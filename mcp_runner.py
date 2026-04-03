"""
Standalone MCP runner for local development.

Usage:
    # MCP Inspector (interactive testing):
    mcp dev backend/mcp_runner.py

    # Stdio transport (for Claude Desktop / Cursor local config):
    python backend/mcp_runner.py
"""

import sys
import os

# Allow imports from backend/ when run from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from app.mcp_server import mcp

if __name__ == "__main__":
    mcp.run()

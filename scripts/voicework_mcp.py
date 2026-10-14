#!/usr/bin/env python3
"""Launch the Voicework MCP server from any working directory (for MCP client configs)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voicework.mcp import main  # noqa: E402

raise SystemExit(main())

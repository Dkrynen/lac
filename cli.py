#!/usr/bin/env python3
"""Leaf — chat with and manage local LLMs via Ollama."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backend.cli_main import main

if __name__ == "__main__":
    main()

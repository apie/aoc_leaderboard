#!/bin/bash
cd "$(dirname "$0")"
DEBUG=1 ~/.local/bin/uv run main.py

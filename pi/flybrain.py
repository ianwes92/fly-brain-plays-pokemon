#!/usr/bin/env python3
"""Entry point: ./flybrain.py <command>"""
import sys

from flybrain.cli import main

if __name__ == "__main__":
    sys.exit(main())

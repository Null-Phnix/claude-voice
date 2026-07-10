#!/usr/bin/env python3
"""Legacy entry point — the real code lives in claude_voice.py.

Kept so old hooks pointing at speak.py keep working.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from claude_voice import main

if __name__ == "__main__":
    main()

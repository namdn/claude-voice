#!/usr/bin/env python3
"""Voice Input for Claude Code — Desktop floating overlay."""

import logging
import sys

from src.config import load_config, validate_config


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config = load_config()
    errors = validate_config(config)
    if errors:
        print("Configuration errors:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    # Import here to avoid loading heavy deps during config validation
    from src.app import VoiceClaudeApp

    app = VoiceClaudeApp(config)
    app.run()


if __name__ == "__main__":
    main()

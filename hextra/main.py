"""Application entrypoint wiring."""

import sys
import traceback


def run():
    """Start Hextra through the legacy core during the module split."""
    try:
        from . import legacy

        legacy._boot()
        legacy._ensure_elevated_start()
        return legacy.main()
    except Exception:
        traceback.print_exc()
        input("\nThe application crashed. Press Enter to exit.")
        return 1


if __name__ == "__main__":
    raise SystemExit(run())


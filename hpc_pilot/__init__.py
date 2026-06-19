"""
HPC Pilot - Standalone AI agent for HPC cluster management.

This package embeds and extends Hermes Agent to provide a complete
AI agent for HPC cluster management with no external dependencies
beyond what's specified in requirements.
"""

from __future__ import annotations

from typing import Optional
__version__ = "1.0.0"

def main() -> int:
    """Main entry point for the hpc-pilot command."""
    from hpc_pilot.cli import main as cli_main
    
    # Check if we're running as a script or module
    if len(__import__("sys").argv) > 1:
        return cli_main()
    return cli_main()


if __name__ == "__main__":
    import sys
    sys.exit(main())
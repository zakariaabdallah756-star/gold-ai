"""Start the Trading OS & Journal web app.

Usage (from any folder):
    python trading_os/run.py              # your journal, opens the browser
    python trading_os/run.py --demo       # separate demo journal with sample trades
    python trading_os/run.py --help       # all options
"""

import sys
from pathlib import Path

# Run as a script: make the folder that contains trading_os/ importable, and
# drop trading_os/ itself from the path so its modules can't shadow others.
sys.path[0] = str(Path(__file__).resolve().parent.parent)

from trading_os.cli import main  # noqa: E402

if __name__ == "__main__":
    main()

"""Start the Trading OS & Journal web app.

Usage:
    python run_trading_os.py              # your journal, opens the browser
    python run_trading_os.py --demo       # separate demo journal with sample trades
    python run_trading_os.py --help       # all options
"""

from trading_os.cli import main

if __name__ == "__main__":
    main()

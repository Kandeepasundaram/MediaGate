#!/usr/bin/env python3
"""Entry point alias for the Windows notification agent (see windows_toast.py).

Kept as a separate file to match the name used for the Task Scheduler entry
in setup_windows.bat; the implementation lives in windows_toast.py.
"""
from windows_toast import main

if __name__ == "__main__":
    main()

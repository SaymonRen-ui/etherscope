"""EtherScope — анализатор Wi-Fi / Bluetooth / BLE. Точка входа."""

import ctypes
import sys


def _dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def main():
    _dpi_awareness()
    from ui.app import ScannerApp
    app = ScannerApp()
    app.mainloop()


if __name__ == "__main__":
    sys.exit(main())

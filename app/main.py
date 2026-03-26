import sys

from PyQt5.QtWidgets import QApplication

from app.ui.main_window import WizardWindow


def main() -> int:
    app = QApplication(sys.argv)
    win = WizardWindow()
    win.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())


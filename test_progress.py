import sys
import time
from PySide6.QtWidgets import QApplication, QMainWindow, QProgressDialog
from PySide6.QtCore import Qt

app = QApplication(sys.argv)
win = QMainWindow()
win.resize(400, 300)
win.show()

# Pokaż okno z progressbarem
prog = QProgressDialog("Loading...", "Cancel", 0, 100, win)
prog.setWindowModality(Qt.WindowModal)
prog.setMinimumDuration(0)
prog.setValue(50)

# Ustawienie marginesów przez QSS
app.setStyleSheet("""
QProgressDialog {
    min-width: 300px;
}
QProgressDialog > QPushButton {
    margin-top: 15px; /* Dodajemy odstęp nad przyciskiem 'Anuluj' */
}
""")

prog.show()

# Blokada by okno się pokazało na screenie Xvfb jeśli potrzeba,
# w skrypcie odpali się asynchronicznie.

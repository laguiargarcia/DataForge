"""Notice shared by the DataForge windows: the local server stopped answering."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton

from dataforge import instance


class ServerLostBanner(QFrame):
    """"DataForge desligado" with a **Ligar de novo** button; hidden until needed.

    ``restarted`` fires after ``ensure_server`` succeeded (the banner hides itself first),
    so the owning window can reload what it shows.
    """

    restarted = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        self.label = QLabel("DataForge desligado")
        self.restart_btn = QPushButton("Ligar de novo")
        self.restart_btn.clicked.connect(self._on_restart_clicked)
        layout.addWidget(self.label)
        layout.addStretch()
        layout.addWidget(self.restart_btn)
        self.setStyleSheet("QFrame { background: #f8d7da; } QLabel { color: #721c24; }")
        self.hide()

    def _on_restart_clicked(self) -> None:
        """Start the server again. ensure_server may block up to ~20 s: busy cursor, button off."""
        self.restart_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            instance.ensure_server()
        except instance.InstanceError as exc:
            QMessageBox.warning(self, "DataForge", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.restart_btn.setEnabled(True)
        self.hide()
        self.restarted.emit()

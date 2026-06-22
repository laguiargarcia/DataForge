"""Dashboards panel placeholder — filled in Phase 2.2."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class Panel(QWidget):
    """Placeholder for the Dashboards panel (Phase 2.2)."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        label = QLabel("Dashboards — coming in Phase 2.2")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

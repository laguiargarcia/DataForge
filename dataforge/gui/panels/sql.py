"""SQL Workbench panel placeholder — filled in Phase 2.1."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class Panel(QWidget):
    """Placeholder for the SQL Workbench panel (Phase 2.1)."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        label = QLabel("SQL Workbench — coming in Phase 2.1")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

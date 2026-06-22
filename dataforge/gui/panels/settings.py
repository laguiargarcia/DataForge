"""Settings panel placeholder — filled in Phase 1.5+."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class Panel(QWidget):
    """Placeholder for the Settings panel (Phase 1.5+)."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        label = QLabel("Settings — coming in Phase 1.5")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

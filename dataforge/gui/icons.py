"""The DataForge placeholder icon: tray icon of the background process, default app window icon."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

_SIZE = 32
_BACKGROUND = "#4A90D9"


def default_icon() -> QIcon:
    """A blue square with a white bold "D", so it stands out in the Windows tray."""
    pixmap = QPixmap(_SIZE, _SIZE)
    pixmap.fill(QColor(_BACKGROUND))
    painter = QPainter(pixmap)
    try:
        font = QFont()
        font.setBold(True)
        font.setPixelSize(_SIZE * 3 // 4)
        painter.setFont(font)
        painter.setPen(QColor("white"))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "D")
    finally:
        painter.end()
    return QIcon(pixmap)

"""Smoke tests: dataforge.gui importable; QApplication starts offscreen."""
import pytest

PySide6 = pytest.importorskip("PySide6")


def test_run_app_importable():
    from dataforge.gui import run_app
    assert callable(run_app)


def test_qapplication_offscreen():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QGuiApplication
    app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
    QGuiApplication.setQuitOnLastWindowClosed(False)
    assert app is not None

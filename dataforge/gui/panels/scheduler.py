"""Scheduler panel — Phase 1.5 Plan 05: full CRUD UI + cron picker."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Optional

from croniter import croniter as _croniter

from PySide6.QtCore import QByteArray, QDate, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


# ---------------------------------------------------------------------------
# Module-level pure helpers (importable without constructing Qt widgets)
# ---------------------------------------------------------------------------

_DAY_NAMES = {
    0: "Sunday",
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
}


def cron_is_valid(expr: str) -> bool:
    """Return True if *expr* is a valid 5-field cron expression."""
    return _croniter.is_valid(expr)


def next_run(expr: str, base: Optional[datetime] = None) -> Optional[datetime]:
    """Return the next scheduled datetime after *base* (default: utcnow).

    Returns None if *expr* is invalid (T-01.5-17 — no crash on bad cron).
    """
    if not cron_is_valid(expr):
        return None
    try:
        return _croniter(expr, base or datetime.utcnow()).get_next(datetime)
    except Exception:
        return None


def _local_offset() -> timedelta:
    """Current OS UTC offset (positive east of UTC). Handles DST automatically."""
    return datetime.now().astimezone().utcoffset() or timedelta(0)


def utc_to_local_dt(dt: datetime) -> datetime:
    """Shift a naive UTC datetime to naive OS-local."""
    return dt + _local_offset()


def _shift_cron(cron: str, offset: timedelta) -> str:
    """Shift cron minute/hour by *offset*. Wildcards/ranges/lists pass through."""
    parts = cron.strip().split()
    if len(parts) != 5:
        return cron
    minute, hour, dom, month, dow = parts
    if not (minute.isdigit() and hour.isdigit()):
        return cron
    delta_min = int(offset.total_seconds() // 60)
    total = int(hour) * 60 + int(minute) + delta_min
    day_shift, total = divmod(total, 24 * 60)
    new_hour, new_minute = divmod(total, 60)
    if day_shift != 0 and dow.isdigit():
        dow = str((int(dow) + day_shift) % 7)
    return f"{new_minute} {new_hour} {dom} {month} {dow}"


def utc_to_local_cron(cron: str) -> str:
    return _shift_cron(cron, _local_offset())


def local_to_utc_cron(cron: str) -> str:
    return _shift_cron(cron, -_local_offset())


def preset_to_cron(
    preset_key: str,
    hour: int,
    minute: int,
    weekday: Optional[int] = None,
    dom: Optional[int] = None,
) -> tuple[str, str]:
    """Compute (cron_str, description) from preset key + time/day fields.

    Presets:
      "hourly"   → "0 * * * *"       / "Every hour"
      "daily"    → "M H * * *"        / "Every day at HH:MM"
      "weekly"   → "M H * * WD"       / "Every {DayName} at HH:MM"
      "monthly"  → "M H D * *"        / "Monthly on day D at HH:MM"
      "advanced" → ("", "Custom schedule")  — caller supplies raw cron string

    Cron strings are computed from fields — never a static lookup table.
    """
    h = int(hour)
    m = int(minute)
    time_str = f"{h:02d}:{m:02d}"

    if preset_key == "hourly":
        return ("0 * * * *", "Every hour")

    if preset_key == "daily":
        return (f"{m} {h} * * *", f"Every day at {time_str}")

    if preset_key == "weekly":
        wd = int(weekday) if weekday is not None else 1
        day_name = _DAY_NAMES.get(wd, f"day {wd}")
        return (f"{m} {h} * * {wd}", f"Every {day_name} at {time_str}")

    if preset_key == "monthly":
        d = int(dom) if dom is not None else 1
        return (f"{m} {h} {d} * *", f"Monthly on day {d} at {time_str}")

    # Advanced / raw mode — Pitfall 1: croniter has no humanizer
    return ("", "Custom schedule")


# ---------------------------------------------------------------------------
# CronPicker widget
# ---------------------------------------------------------------------------

class CronPicker(QWidget):
    """Friendly cron entry widget: preset ComboBox + time/day inputs + Advanced raw field.

    Emits no custom signals; parent reads .current_cron() and .current_description()
    after any change (via the _on_changed callback if set).
    """

    PRESET_KEYS = ["hourly", "daily", "weekly", "monthly", "advanced"]
    PRESET_LABELS = ["Every hour", "Every day at…", "Every week on…", "Monthly on day…", "Advanced (raw cron)"]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._on_changed_cb = None
        # "utc" = entered/displayed times are UTC (wire format).
        # "local" = entered/displayed times are OS-local; converted to UTC at boundaries.
        self._display_mode = "utc"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Preset selector
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Schedule:"))
        self._preset_combo = QComboBox()
        self._preset_combo.addItems(self.PRESET_LABELS)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self._preset_combo)
        preset_row.addStretch()
        layout.addLayout(preset_row)

        # Time inputs (hour:minute)
        time_row = QHBoxLayout()
        self._hour_edit = QLineEdit("6")
        self._hour_edit.setFixedWidth(40)
        self._hour_edit.setPlaceholderText("HH")
        self._hour_edit.textChanged.connect(self._on_changed)
        self._minute_edit = QLineEdit("0")
        self._minute_edit.setFixedWidth(40)
        self._minute_edit.setPlaceholderText("MM")
        self._minute_edit.textChanged.connect(self._on_changed)
        time_row.addWidget(QLabel("Time (HH:MM):"))
        time_row.addWidget(self._hour_edit)
        time_row.addWidget(QLabel(":"))
        time_row.addWidget(self._minute_edit)
        time_row.addStretch()
        self._time_widget = QWidget()
        self._time_widget.setLayout(time_row)
        layout.addWidget(self._time_widget)

        # Weekday selector (weekly preset only)
        day_row = QHBoxLayout()
        day_row.addWidget(QLabel("Day:"))
        self._day_combo = QComboBox()
        for wd_num, wd_name in _DAY_NAMES.items():
            self._day_combo.addItem(wd_name, wd_num)
        self._day_combo.setCurrentIndex(1)  # Monday default
        self._day_combo.currentIndexChanged.connect(self._on_changed)
        day_row.addWidget(self._day_combo)
        day_row.addStretch()
        self._day_widget = QWidget()
        self._day_widget.setLayout(day_row)
        layout.addWidget(self._day_widget)

        # Day-of-month selector (monthly preset only)
        dom_row = QHBoxLayout()
        dom_row.addWidget(QLabel("Day of month:"))
        self._dom_edit = QLineEdit("1")
        self._dom_edit.setFixedWidth(40)
        self._dom_edit.textChanged.connect(self._on_changed)
        dom_row.addWidget(self._dom_edit)
        dom_row.addStretch()
        self._dom_widget = QWidget()
        self._dom_widget.setLayout(dom_row)
        layout.addWidget(self._dom_widget)

        # Raw cron input (advanced only)
        raw_row = QHBoxLayout()
        raw_row.addWidget(QLabel("Cron expression:"))
        self._raw_edit = QLineEdit()
        self._raw_edit.setPlaceholderText("e.g. 0 6 * * *")
        self._raw_edit.textChanged.connect(self._on_changed)
        raw_row.addWidget(self._raw_edit)
        self._raw_widget = QWidget()
        self._raw_widget.setLayout(raw_row)
        layout.addWidget(self._raw_widget)

        # Preview label
        self._preview_label = QLabel("")
        layout.addWidget(self._preview_label)

        # Initial visibility
        self._on_preset_changed(0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _preset_key(self) -> str:
        idx = self._preset_combo.currentIndex()
        if 0 <= idx < len(self.PRESET_KEYS):
            return self.PRESET_KEYS[idx]
        return "advanced"

    def _on_preset_changed(self, _index: int) -> None:
        key = self._preset_key()
        is_hourly = key == "hourly"
        is_daily = key == "daily"
        is_weekly = key == "weekly"
        is_monthly = key == "monthly"
        is_advanced = key == "advanced"

        self._time_widget.setVisible(not is_hourly and not is_advanced)
        self._day_widget.setVisible(is_weekly)
        self._dom_widget.setVisible(is_monthly)
        self._raw_widget.setVisible(is_advanced)
        self._on_changed()

    def _on_changed(self, *_args) -> None:
        key = self._preset_key()
        try:
            h = int(self._hour_edit.text() or 0)
            m = int(self._minute_edit.text() or 0)
        except ValueError:
            h, m = 0, 0

        wd = self._day_combo.currentData()
        try:
            dom = int(self._dom_edit.text() or 1)
        except ValueError:
            dom = 1

        if key == "advanced":
            raw = self._raw_edit.text().strip()
            cron_str = raw
            description = "Custom schedule"
        else:
            cron_str, description = preset_to_cron(key, h, m, weekday=wd, dom=dom)

        # Hour/minute entered above are in the picker's display mode.
        # next_run operates on UTC cron, so convert when displaying as local.
        utc_cron = local_to_utc_cron(cron_str) if self._display_mode == "local" else cron_str

        nxt = next_run(utc_cron) if utc_cron else None
        tz_label = "local" if self._display_mode == "local" else "UTC"
        if nxt is not None:
            shown = utc_to_local_dt(nxt) if self._display_mode == "local" else nxt
            nxt_str = shown.strftime(f"%Y-%m-%d %H:%M {tz_label}")
            self._preview_label.setText(f"{description} — next run: {nxt_str}")
        elif cron_str:
            self._preview_label.setText(f"{description} — next run: invalid cron")
        else:
            self._preview_label.setText(description)

        if self._on_changed_cb:
            self._on_changed_cb()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def current_cron(self) -> str:
        """Return the current cron expression as UTC (empty string if advanced+empty)."""
        key = self._preset_key()
        if key == "advanced":
            raw = self._raw_edit.text().strip()
            if self._display_mode == "local" and raw:
                return local_to_utc_cron(raw)
            return raw
        try:
            h = int(self._hour_edit.text() or 0)
            m = int(self._minute_edit.text() or 0)
        except ValueError:
            h, m = 0, 0
        wd = self._day_combo.currentData()
        try:
            dom = int(self._dom_edit.text() or 1)
        except ValueError:
            dom = 1
        cron_str, _ = preset_to_cron(key, h, m, weekday=wd, dom=dom)
        if self._display_mode == "local":
            cron_str = local_to_utc_cron(cron_str)
        return cron_str

    def current_description(self) -> str:
        """Return the plain-English description for the current selection."""
        key = self._preset_key()
        if key == "advanced":
            return "Custom schedule"
        try:
            h = int(self._hour_edit.text() or 0)
            m = int(self._minute_edit.text() or 0)
        except ValueError:
            h, m = 0, 0
        wd = self._day_combo.currentData()
        try:
            dom = int(self._dom_edit.text() or 1)
        except ValueError:
            dom = 1
        _, description = preset_to_cron(key, h, m, weekday=wd, dom=dom)
        return description

    def set_cron(self, cron_str: str) -> None:
        """Pre-fill the picker from a UTC cron string (uses Advanced mode).

        In "local" display mode the UTC string is shifted so the raw field
        shows the user's local equivalent.
        """
        adv_idx = self.PRESET_KEYS.index("advanced")
        self._preset_combo.setCurrentIndex(adv_idx)
        shown = utc_to_local_cron(cron_str) if self._display_mode == "local" and cron_str else cron_str
        self._raw_edit.setText(shown)

    def set_display_mode(self, mode: str) -> None:
        """Switch input/preview between "local" and "utc".

        Re-renders the raw cron field so the user sees the same instant in the
        new reference frame.
        """
        if mode not in ("local", "utc") or mode == self._display_mode:
            return
        prev = self._display_mode
        # Convert the raw advanced field across modes without changing the UTC instant it represents.
        raw = self._raw_edit.text().strip()
        if raw:
            if prev == "local":
                utc_raw = local_to_utc_cron(raw)
            else:
                utc_raw = raw
            self._display_mode = mode
            shown = utc_to_local_cron(utc_raw) if mode == "local" else utc_raw
            self._raw_edit.setText(shown)
        else:
            self._display_mode = mode
        self._on_changed()

    def set_on_changed(self, callback) -> None:
        """Register a zero-argument callback called whenever picker changes."""
        self._on_changed_cb = callback


# ---------------------------------------------------------------------------
# JobParamsDialog — typed parameter editor dialog
# ---------------------------------------------------------------------------

_PARAM_TYPES = ["string", "int", "float", "bool", "date", "json"]


class JobParamsDialog(QDialog):
    """ADF-style dialog for editing per-trigger parameter values for one job.

    ``declared_params`` is the JSON-decoded ``parameters`` list from the jobs
    API: ``[{"name", "type", "default", "description"}, ...]``.
    ``current_values`` is the dict previously cached on the row's Configure
    button (``{}`` on first open).

    The dialog renders one typed widget per declared parameter. A free-form
    ``+ Add parameter`` row allows adding ad-hoc keys not in the declaration.
    """

    def __init__(
        self,
        declared_params: list[dict],
        current_values: dict,
        workspace: str,
        job: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Configure parameters — {workspace}/{job}")
        self.setMinimumWidth(520)
        self._declared_params = declared_params
        self._current_values = current_values

        layout = QVBoxLayout(self)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Name", "Type", "Value", ""])
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

        # Populate declared parameter rows
        for p in declared_params:
            self._insert_declared_row(p, current_values.get(p.get("name", ""), p.get("default")))

        add_btn = QPushButton("+ Add parameter")
        add_btn.clicked.connect(self._add_free_row)
        layout.addWidget(add_btn)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self._on_ok)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    # ------------------------------------------------------------------
    # Row factories
    # ------------------------------------------------------------------

    def _make_value_widget(self, type_str: str, value=None) -> QWidget:
        """Create a typed input widget pre-filled with *value*."""
        if type_str == "int":
            w = QSpinBox()
            w.setRange(-2_147_483_648, 2_147_483_647)
            if value is not None:
                try:
                    w.setValue(int(value))
                except (ValueError, TypeError):
                    pass
        elif type_str == "float":
            w = QDoubleSpinBox()
            w.setRange(-1e15, 1e15)
            w.setDecimals(6)
            if value is not None:
                try:
                    w.setValue(float(value))
                except (ValueError, TypeError):
                    pass
        elif type_str == "bool":
            w = QCheckBox()
            if value is not None:
                w.setChecked(bool(value))
        elif type_str == "date":
            w = QDateEdit()
            w.setCalendarPopup(True)
            if value is not None:
                try:
                    parts = str(value).split("-")
                    if len(parts) == 3:
                        w.setDate(QDate(int(parts[0]), int(parts[1]), int(parts[2])))
                except (ValueError, TypeError):
                    pass
        elif type_str == "json":
            w = QPlainTextEdit()
            w.setFixedHeight(60)
            if value is not None:
                try:
                    w.setPlainText(json.dumps(value) if not isinstance(value, str) else value)
                except Exception:
                    w.setPlainText(str(value))
        else:
            # "string" and unknown types
            w = QLineEdit()
            if value is not None:
                w.setText(str(value))
        return w

    def _read_value_widget(self, widget: QWidget, type_str: str):
        """Extract the typed Python value from a typed widget."""
        if type_str == "int":
            return widget.value()
        if type_str == "float":
            return widget.value()
        if type_str == "bool":
            return widget.isChecked()
        if type_str == "date":
            d = widget.date()
            return f"{d.year():04d}-{d.month():02d}-{d.day():02d}"
        if type_str == "json":
            return widget.toPlainText()  # validated on OK
        return widget.text()  # string / fallback

    def _insert_declared_row(self, param: dict, value=None) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)

        name = param.get("name", "")
        type_str = param.get("type", "string")

        name_label = QLabel(name)
        name_label.setMargin(4)
        self._table.setCellWidget(row, 0, name_label)

        type_label = QLabel(type_str)
        type_label.setMargin(4)
        self._table.setCellWidget(row, 1, type_label)

        value_widget = self._make_value_widget(type_str, value)
        self._table.setCellWidget(row, 2, value_widget)

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(28)
        remove_btn.clicked.connect(lambda _checked=False, r=row: self._remove_row(r))
        self._table.setCellWidget(row, 3, remove_btn)

        # Store type on name label for retrieval
        name_label.setProperty("param_type", type_str)
        name_label.setProperty("is_declared", True)

    def _add_free_row(self) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)

        name_edit = QLineEdit()
        name_edit.setPlaceholderText("param name")
        self._table.setCellWidget(row, 0, name_edit)

        type_combo = QComboBox()
        type_combo.addItems(_PARAM_TYPES)
        self._table.setCellWidget(row, 1, type_combo)

        value_widget = self._make_value_widget("string")
        self._table.setCellWidget(row, 2, value_widget)

        def _on_type_change(text: str) -> None:
            new_w = self._make_value_widget(text)
            self._table.setCellWidget(row, 2, new_w)

        type_combo.currentTextChanged.connect(_on_type_change)

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(28)
        remove_btn.clicked.connect(lambda _checked=False, r=row: self._remove_row(r))
        self._table.setCellWidget(row, 3, remove_btn)

    def _remove_row(self, row: int) -> None:
        if 0 <= row < self._table.rowCount():
            self._table.removeRow(row)

    # ------------------------------------------------------------------
    # OK handler — validates JSON fields before accepting
    # ------------------------------------------------------------------

    def _on_ok(self) -> None:
        # Validate JSON cells before accepting (T-01.5.06-01)
        for row in range(self._table.rowCount()):
            type_widget = self._table.cellWidget(row, 1)
            # Determine type for this row
            if isinstance(type_widget, QLabel):
                type_str = type_widget.text()
            elif isinstance(type_widget, QComboBox):
                type_str = type_widget.currentText()
            else:
                continue
            if type_str == "json":
                value_widget = self._table.cellWidget(row, 2)
                if isinstance(value_widget, QPlainTextEdit):
                    raw = value_widget.toPlainText().strip()
                    if raw:
                        try:
                            json.loads(raw)
                        except json.JSONDecodeError as exc:
                            QMessageBox.warning(
                                self,
                                "Invalid JSON",
                                f"Row {row + 1}: invalid JSON — {exc}\n\nPlease fix before saving.",
                            )
                            return  # Keep dialog open
        self.accept()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def result_params(self) -> dict:
        """Return {name: value} for all rows. Called after exec() == Accepted."""
        result = {}
        for row in range(self._table.rowCount()):
            name_widget = self._table.cellWidget(row, 0)
            type_widget = self._table.cellWidget(row, 1)
            value_widget = self._table.cellWidget(row, 2)

            if name_widget is None or value_widget is None:
                continue

            # Determine name
            if isinstance(name_widget, QLabel):
                name = name_widget.text()
                type_str = name_widget.property("param_type") or "string"
            elif isinstance(name_widget, QLineEdit):
                name = name_widget.text().strip()
                type_str = type_widget.currentText() if isinstance(type_widget, QComboBox) else "string"
            else:
                continue

            if not name:
                continue

            # Determine value
            if type_str == "json":
                raw = value_widget.toPlainText().strip() if isinstance(value_widget, QPlainTextEdit) else "{}"
                try:
                    value = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    value = raw
            else:
                value = self._read_value_widget(value_widget, type_str)

            result[name] = value
        return result


# ---------------------------------------------------------------------------
# Scheduler Panel
# ---------------------------------------------------------------------------

class Panel(QWidget):
    """Scheduler panel: trigger list + detail/edit + New Trigger form."""

    PAGE_LIST = 0
    PAGE_DETAIL = 1

    def __init__(self) -> None:
        super().__init__()
        self._port = int(os.environ.get("DATAFORGE_PORT", 8000))
        self._nam = QNetworkAccessManager(self)
        self._current_workspace = ""
        # Cached trigger data indexed by row number
        self._trigger_data: list[dict] = []
        # State for detail view: None = create mode, str = edit mode (trigger name)
        self._detail_trigger_name: Optional[str] = None
        # View mode for time fields/columns: "local" matches user intuition;
        # cron is always stored in UTC on the wire.
        self._display_mode = "local"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- Header bar ----
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 8, 8, 8)

        self._back_btn = QPushButton("← Back")
        self._back_btn.setVisible(False)
        self._back_btn.clicked.connect(self._go_back)
        header_layout.addWidget(self._back_btn)

        self._breadcrumb = QLabel("Scheduler")
        header_layout.addWidget(self._breadcrumb)
        header_layout.addStretch()
        outer.addWidget(header)

        # ---- Stacked widget: LIST | DETAIL ----
        self._stack = QStackedWidget()

        # PAGE_LIST = 0
        self._stack.addWidget(self._build_list_page())

        # PAGE_DETAIL = 1
        self._stack.addWidget(self._build_detail_page())

        outer.addWidget(self._stack, stretch=1)

    # ------------------------------------------------------------------
    # Page builders
    # ------------------------------------------------------------------

    def _build_list_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(4)

        # Toolbar: View timezone toggle + New Trigger button
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("View:"))
        self._tz_combo = QComboBox()
        self._tz_combo.addItem("Local", "local")
        self._tz_combo.addItem("UTC", "utc")
        # Default ("local") is set in __init__; select the matching index.
        self._tz_combo.setCurrentIndex(0 if self._display_mode == "local" else 1)
        self._tz_combo.currentIndexChanged.connect(self._on_tz_changed)
        toolbar.addWidget(self._tz_combo)
        toolbar.addStretch()
        self._new_trigger_btn = QPushButton("New Trigger")
        self._new_trigger_btn.clicked.connect(self._open_create_form)
        toolbar.addWidget(self._new_trigger_btn)
        layout.addLayout(toolbar)

        # Trigger table
        self._trigger_table = QTableWidget(0, 5)
        self._trigger_table.setHorizontalHeaderLabels(
            ["Name", "Cron", "Enabled", "Last Run", "Next Run"]
        )
        self._trigger_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._trigger_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._trigger_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._trigger_table.verticalHeader().setVisible(False)
        self._trigger_table.cellClicked.connect(self._on_trigger_clicked)
        layout.addWidget(self._trigger_table, stretch=1)

        return page

    def _build_detail_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Error label (hidden until API returns 4xx)
        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #ff6b6b;")
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)

        # Trigger name field (read-only in edit mode, editable in create mode)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("trigger-name (alphanumeric, hyphens, underscores)")
        form.addRow("Name:", self._name_edit)

        # Enabled checkbox
        self._enabled_check = QCheckBox("Enabled")
        self._enabled_check.setChecked(True)
        form.addRow("", self._enabled_check)

        layout.addLayout(form)

        # Cron picker — inherits the Panel's current display mode
        layout.addWidget(QLabel("Schedule:"))
        self._cron_picker = CronPicker()
        self._cron_picker.set_display_mode(self._display_mode)
        layout.addWidget(self._cron_picker)

        # Jobs list
        layout.addWidget(QLabel("Jobs:"))
        self._jobs_table = QTableWidget(0, 4)
        self._jobs_table.setHorizontalHeaderLabels(["Workspace", "Job", "Parameters", ""])
        self._jobs_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._jobs_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._jobs_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._jobs_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._jobs_table.setColumnWidth(2, 160)
        self._jobs_table.setColumnWidth(3, 36)
        self._jobs_table.verticalHeader().setVisible(False)
        self._jobs_table.setMinimumHeight(120)
        layout.addWidget(self._jobs_table, stretch=1)

        add_job_btn = QPushButton("+ Add Job")
        add_job_btn.clicked.connect(self._add_job_row)
        layout.addWidget(add_job_btn)

        # Action buttons
        btn_row = QHBoxLayout()
        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._on_save)
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setStyleSheet("color: #ff6b6b;")
        self._delete_btn.clicked.connect(self._on_delete)
        btn_row.addStretch()
        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(self._delete_btn)
        layout.addLayout(btn_row)

        return page

    # ------------------------------------------------------------------
    # Workspace slot — connected by MainWindow.workspace_changed signal
    # ------------------------------------------------------------------

    def set_workspace(self, name: str) -> None:
        """Called by MainWindow.workspace_changed signal."""
        if not name:
            return
        self._current_workspace = name
        self._load_triggers()

    # ------------------------------------------------------------------
    # List page — API fetch + populate table
    # ------------------------------------------------------------------

    def _load_triggers(self) -> None:
        url = QUrl(
            f"http://127.0.0.1:{self._port}"
            f"/api/workspaces/{self._current_workspace}/triggers"
        )
        req = QNetworkRequest(url)
        reply = self._nam.get(req)
        reply.finished.connect(lambda: self._on_triggers_loaded(reply))

    def _on_triggers_loaded(self, reply) -> None:
        try:
            data = json.loads(bytes(reply.readAll()).decode())
        except Exception:
            data = []
        finally:
            reply.deleteLater()

        if not isinstance(data, list):
            data = []

        self._trigger_data = data
        self._render_trigger_rows()

    def _render_trigger_rows(self) -> None:
        """Refresh the trigger table using the cached data + current display mode."""
        tz_label = "local" if self._display_mode == "local" else "UTC"
        self._trigger_table.setRowCount(0)
        for i, trigger in enumerate(self._trigger_data):
            self._trigger_table.insertRow(i)
            cron_expr = trigger.get("cron", "")
            # cron in payload is UTC; show user-mode equivalent
            shown_cron = (
                utc_to_local_cron(cron_expr)
                if self._display_mode == "local" and cron_expr
                else cron_expr
            )

            nxt = next_run(cron_expr)
            if nxt is not None:
                shown_nxt = utc_to_local_dt(nxt) if self._display_mode == "local" else nxt
                nxt_str = shown_nxt.strftime(f"%Y-%m-%d %H:%M {tz_label}")
            else:
                nxt_str = "—"

            items = [
                QTableWidgetItem(trigger.get("name", "")),
                QTableWidgetItem(shown_cron),
                QTableWidgetItem("Yes" if trigger.get("enabled", True) else "No"),
                QTableWidgetItem(trigger.get("last_run", "—") or "—"),
                QTableWidgetItem(nxt_str),
            ]
            for col, item in enumerate(items):
                self._trigger_table.setItem(i, col, item)

    def _on_tz_changed(self, _index: int) -> None:
        """User flipped the View toggle: propagate to picker + re-render the list."""
        mode = self._tz_combo.currentData()
        if not mode or mode == self._display_mode:
            return
        self._display_mode = mode
        if hasattr(self, "_cron_picker"):
            self._cron_picker.set_display_mode(mode)
        self._render_trigger_rows()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _navigate_to(self, page: int, breadcrumb: str = "Scheduler") -> None:
        self._breadcrumb.setText(breadcrumb)
        self._back_btn.setVisible(page != self.PAGE_LIST)
        self._stack.setCurrentIndex(page)

    def _go_back(self) -> None:
        self._navigate_to(self.PAGE_LIST, "Scheduler")
        if self._current_workspace:
            self._load_triggers()

    # ------------------------------------------------------------------
    # List page — row click → detail view
    # ------------------------------------------------------------------

    def _on_trigger_clicked(self, row: int, col: int) -> None:
        if row >= len(self._trigger_data):
            return
        trigger = self._trigger_data[row]
        self._open_detail(trigger)

    def _open_detail(self, trigger: dict) -> None:
        """Switch to PAGE_DETAIL pre-filled with trigger data (edit mode)."""
        self._detail_trigger_name = trigger.get("name", "")
        self._error_label.setVisible(False)

        # Populate form
        self._name_edit.setText(self._detail_trigger_name)
        self._name_edit.setReadOnly(True)  # Name immutable in edit mode
        self._enabled_check.setChecked(trigger.get("enabled", True))
        self._cron_picker.set_cron(trigger.get("cron", ""))

        # Populate jobs table
        self._jobs_table.setRowCount(0)
        for entry in trigger.get("jobs", []):
            self._append_job_row(
                entry.get("workspace", ""),
                entry.get("job", ""),
                entry.get("params", {}),
            )

        self._delete_btn.setVisible(True)
        self._navigate_to(self.PAGE_DETAIL, f"Scheduler › {self._detail_trigger_name}")

    # ------------------------------------------------------------------
    # New Trigger form
    # ------------------------------------------------------------------

    def _open_create_form(self) -> None:
        """Switch to PAGE_DETAIL in create mode (empty form, editable name)."""
        self._detail_trigger_name = None
        self._error_label.setVisible(False)

        self._name_edit.clear()
        self._name_edit.setReadOnly(False)
        self._enabled_check.setChecked(True)
        # Reset cron picker to daily default
        self._cron_picker._preset_combo.setCurrentIndex(1)
        self._jobs_table.setRowCount(0)
        self._delete_btn.setVisible(False)

        self._navigate_to(self.PAGE_DETAIL, "Scheduler › New Trigger")

    # ------------------------------------------------------------------
    # Jobs table helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Jobs table HTTP helpers
    # ------------------------------------------------------------------

    def _fetch_workspaces(self, callback) -> None:
        """Fetch workspace list from GET /api/workspaces/ and call callback(list[dict])."""
        url = QUrl(f"http://127.0.0.1:{self._port}/api/workspaces/")
        req = QNetworkRequest(url)
        reply = self._nam.get(req)

        def _done():
            try:
                data = json.loads(bytes(reply.readAll()).decode())
            except Exception:
                data = []
            finally:
                reply.deleteLater()
            callback(data if isinstance(data, list) else [])

        reply.finished.connect(_done)

    def _fetch_jobs_for_ws(self, ws: str, callback) -> None:
        """Fetch job list for workspace from GET /api/workspaces/{ws}/jobs and call callback(list[dict])."""
        url = QUrl(f"http://127.0.0.1:{self._port}/api/workspaces/{ws}/jobs")
        req = QNetworkRequest(url)
        reply = self._nam.get(req)

        def _done():
            try:
                data = json.loads(bytes(reply.readAll()).decode())
            except Exception:
                data = []
            finally:
                reply.deleteLater()
            callback(data if isinstance(data, list) else [])

        reply.finished.connect(_done)

    # ------------------------------------------------------------------
    # Jobs table helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _params_button_label(params: dict) -> str:
        n = len(params)
        return f"Configure… ({n} params)"

    def _add_job_row(self) -> None:
        ws = self._current_workspace or ""
        self._append_job_row(ws, "", {})

    def _append_job_row(self, workspace: str, job: str, params: dict) -> None:
        row = self._jobs_table.rowCount()
        self._jobs_table.insertRow(row)

        # col 0: workspace combo — populated from API
        combo_ws = QComboBox()
        self._jobs_table.setCellWidget(row, 0, combo_ws)

        # col 1: job combo — populated after workspace selected
        combo_job = QComboBox()
        self._jobs_table.setCellWidget(row, 1, combo_job)

        # col 2: configure button
        btn_cfg = QPushButton(self._params_button_label(params))
        btn_cfg.setProperty("params", params)
        self._jobs_table.setCellWidget(row, 2, btn_cfg)

        # col 3: remove button
        btn_rm = QPushButton("✕")
        btn_rm.setFixedWidth(28)
        btn_rm.clicked.connect(lambda _checked=False, r=row: self._remove_job_row(r))
        self._jobs_table.setCellWidget(row, 3, btn_rm)

        # Wire cascade: workspace change -> reload job combo
        combo_ws.currentIndexChanged.connect(
            lambda _idx, r=row: self._refresh_job_combo(r)
        )

        # Wire configure button
        def _on_configure(_checked=False, r=row):
            combo_j = self._jobs_table.cellWidget(r, 1)
            declared = combo_j.currentData() if combo_j and combo_j.currentIndex() >= 0 else []
            if declared is None:
                declared = []
            btn = self._jobs_table.cellWidget(r, 2)
            current = btn.property("params") or {} if btn else {}
            cw = self._jobs_table.cellWidget(r, 0)
            ws_name = cw.currentText() if cw else ""
            job_name = combo_j.currentText() if combo_j else ""
            dlg = JobParamsDialog(declared, current, ws_name, job_name, parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                new_params = dlg.result_params()
                if btn:
                    btn.setProperty("params", new_params)
                    btn.setText(self._params_button_label(new_params))

        btn_cfg.clicked.connect(_on_configure)

        # Populate workspace combo from API, then pre-select workspace
        def _on_workspaces(ws_list):
            combo_ws.blockSignals(True)
            combo_ws.clear()
            for item in ws_list:
                combo_ws.addItem(item.get("name", ""), item.get("name", ""))
            # Pre-select the specified workspace
            if workspace:
                idx = combo_ws.findText(workspace)
                if idx >= 0:
                    combo_ws.setCurrentIndex(idx)
            combo_ws.blockSignals(False)
            # Trigger job refresh after workspace is set
            self._refresh_job_combo(row, pre_select_job=job)

        self._fetch_workspaces(_on_workspaces)

    def _refresh_job_combo(self, row: int, pre_select_job: str = "") -> None:
        """Repopulate the job combo for *row* from the API, optionally pre-selecting *pre_select_job*."""
        combo_ws = self._jobs_table.cellWidget(row, 0)
        combo_job = self._jobs_table.cellWidget(row, 1)
        if combo_ws is None or combo_job is None:
            return
        ws_name = combo_ws.currentText()
        if not ws_name:
            return

        def _on_jobs(job_list):
            combo_job.blockSignals(True)
            combo_job.clear()
            for j in job_list:
                combo_job.addItem(j.get("name", ""), j.get("parameters", []))
            if pre_select_job:
                idx = combo_job.findText(pre_select_job)
                if idx >= 0:
                    combo_job.setCurrentIndex(idx)
            combo_job.blockSignals(False)
            # Update configure button label count based on current params
            btn = self._jobs_table.cellWidget(row, 2)
            if btn:
                current_params = btn.property("params") or {}
                btn.setText(self._params_button_label(current_params))

        self._fetch_jobs_for_ws(ws_name, _on_jobs)

    def _remove_job_row(self, row: int) -> None:
        """Remove the job row at *row* from the table."""
        if 0 <= row < self._jobs_table.rowCount():
            self._jobs_table.removeRow(row)

    def _collect_jobs(self) -> list[dict]:
        jobs = []
        for row in range(self._jobs_table.rowCount()):
            combo_ws = self._jobs_table.cellWidget(row, 0)
            combo_job = self._jobs_table.cellWidget(row, 1)
            btn_cfg = self._jobs_table.cellWidget(row, 2)
            # col 0: workspace — currentData() stores name, fall back to currentText()
            ws_text = (combo_ws.currentData() or combo_ws.currentText() or "").strip() if combo_ws else ""
            # col 1: job — currentData() stores declared parameters list; use currentText() for job name
            job_text = combo_job.currentText().strip() if combo_job else ""
            params = btn_cfg.property("params") or {} if btn_cfg else {}
            if not ws_text and not job_text:
                continue  # skip blank rows
            jobs.append({"workspace": ws_text, "job": job_text, "params": params})
        return jobs

    # ------------------------------------------------------------------
    # Save / Delete — CRUD via QNetworkAccessManager (T-01.5-16: async only)
    # ------------------------------------------------------------------

    def _build_trigger_payload(self) -> dict:
        return {
            "name": self._name_edit.text().strip(),
            "cron": self._cron_picker.current_cron(),
            "enabled": self._enabled_check.isChecked(),
            "jobs": self._collect_jobs(),
        }

    def _on_save(self) -> None:
        payload = self._build_trigger_payload()
        body = json.dumps(payload).encode()

        if self._detail_trigger_name is None:
            # Create mode → POST
            url = QUrl(
                f"http://127.0.0.1:{self._port}"
                f"/api/workspaces/{self._current_workspace}/triggers"
            )
            req = QNetworkRequest(url)
            req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
            reply = self._nam.post(req, QByteArray(body))
        else:
            # Edit mode → PUT
            name = self._detail_trigger_name
            url = QUrl(
                f"http://127.0.0.1:{self._port}"
                f"/api/workspaces/{self._current_workspace}/triggers/{name}"
            )
            req = QNetworkRequest(url)
            req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
            reply = self._nam.put(req, QByteArray(body))

        reply.finished.connect(lambda: self._on_save_finished(reply))

    def _on_save_finished(self, reply) -> None:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        raw = bytes(reply.readAll()).decode(errors="replace")
        reply.deleteLater()

        if status is None or status >= 400:
            # Surface server error (T-01.5-14 / T-01.5-15)
            try:
                detail = json.loads(raw).get("detail", raw)
            except Exception:
                detail = raw or f"HTTP {status}"
            self._error_label.setText(f"Error: {detail}")
            self._error_label.setVisible(True)
            return

        try:
            warnings = json.loads(raw).get("warnings", [])
        except Exception:
            warnings = []
        if warnings:
            QMessageBox.warning(
                self, "Trigger saved — action needed", "\n\n".join(warnings)
            )

        self._go_back()

    def _on_delete(self) -> None:
        if not self._detail_trigger_name:
            return
        name = self._detail_trigger_name
        url = QUrl(
            f"http://127.0.0.1:{self._port}"
            f"/api/workspaces/{self._current_workspace}/triggers/{name}"
        )
        req = QNetworkRequest(url)
        reply = self._nam.deleteResource(req)
        reply.finished.connect(lambda: self._on_delete_finished(reply))

    def _on_delete_finished(self, reply) -> None:
        reply.deleteLater()
        self._go_back()

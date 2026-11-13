"""Windows desktop shortcut (.lnk) that opens a workspace app window without a console."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .apps import AppInfo

_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# PowerShell writes in the console codepage; force UTF-8 so non-ASCII paths ("Área de Trabalho") survive.
_DESKTOP_SCRIPT = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; [Environment]::GetFolderPath('Desktop')"


def pythonw_path() -> Path:
    """``pythonw.exe`` next to the running interpreter (no console), else the interpreter itself."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return candidate if candidate.exists() else exe


def _q(value: object) -> str:
    """A PowerShell single-quoted string literal: ``'`` is doubled."""
    return "'" + str(value).replace("'", "''") + "'"


def app_arguments(info: AppInfo, home: Path) -> list[str]:
    return ["-m", "dataforge", "app", info.workspace, info.name, "--home", str(home)]


def shortcut_ps_command(  # noqa: PLR0913 -- fixed by the task interface; each argument is one .lnk property
    lnk: Path, target: Path, arguments: str, workdir: Path, icon: Path | None, description: str,
) -> list[str]:
    """``powershell -NoProfile -Command`` that writes ``lnk`` via WScript.Shell.

    -ExecutionPolicy is not needed: the policy governs script files, not ``-Command``.
    """
    parts = [
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_q(lnk)})",
        f"$s.TargetPath = {_q(target)}",
        f"$s.Arguments = {_q(arguments)}",
        f"$s.WorkingDirectory = {_q(workdir)}",
        f"$s.Description = {_q(description)}",
    ]
    if icon is not None:
        parts.append(f"$s.IconLocation = {_q(icon)}")
    parts.append("$s.Save()")
    return ["powershell", "-NoProfile", "-Command", "; ".join(parts)]


def has_usable_icon(info: AppInfo) -> bool:
    """A .lnk can only use an .ico; png and others fall back to the default icon."""
    return info.icon is not None and info.icon.suffix.lower() == ".ico"


def desktop_dir() -> Path:
    """The real Desktop folder (it is often redirected to OneDrive)."""
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _DESKTOP_SCRIPT],
            capture_output=True, encoding="utf-8", check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"Não foi possível executar o PowerShell para achar a Área de Trabalho: {exc}") from exc
    out = (res.stdout or "").strip()
    if res.returncode != 0 or not out:
        raise RuntimeError("Não foi possível descobrir a pasta da Área de Trabalho (PowerShell falhou).")
    return Path(out)


def create_app_shortcut(info: AppInfo, home: Path) -> Path:
    """Create the .lnk on the user's Desktop and return its path."""
    name = _INVALID_FILENAME.sub("_", f"{info.title} ({info.workspace})").strip() + ".lnk"
    lnk = desktop_dir() / name
    cmd = shortcut_ps_command(
        lnk, pythonw_path(), subprocess.list2cmdline(app_arguments(info, home)), home,
        info.icon if has_usable_icon(info) else None, info.title,
    )
    res = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()
        raise RuntimeError(f"Falha ao criar o atalho: {detail}" if detail else "Falha ao criar o atalho.")
    return lnk

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GUI-утилита для управления профилями Codex (auth.json) и синхронизации Windows ↔ WSL.

Запуск:
    python переключатель_аккаунтов_codex.py
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import subprocess
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def _codex_home() -> Path:
    override = os.environ.get("CODEX_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex"


CODEX_HOME = _codex_home()
AUTH_PATH = CODEX_HOME / "auth.json"
PROFILES_DIR = CODEX_HOME / "account_profiles"
BACKUPS_DIR = PROFILES_DIR / "_backups"

# ----------------------------
# WSL ↔ Windows auth bootstrap
# ----------------------------
# Problem: Windows CMD and WSL use different HOME, therefore different ~/.codex/auth.json.
# This script can automatically import Windows auth.json into WSL on first run.
#
# Control (optional):
#   CODEX_WSL_AUTOSYNC=bootstrap  (default)  -> only copy if WSL auth.json is missing
#   CODEX_WSL_AUTOSYNC=newest               -> copy the newer auth.json between Windows and WSL (one-way)
#   CODEX_WSL_AUTOSYNC=off                  -> disable any sync logic
#
# If you explicitly set CODEX_HOME yourself, this script will not override/sync anything.

WINDOWS_CODEX_HOME: Path | None = None


def _is_wsl() -> bool:
    if os.name == "nt":
        return False
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        osrelease = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8", errors="ignore").lower()
        if "microsoft" in osrelease or "wsl" in osrelease:
            return True
    except Exception:
        pass
    try:
        version = Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
        if "microsoft" in version:
            return True
    except Exception:
        pass
    return False


def _run_capture(cmd: list[str], *, timeout_s: float = 3.0) -> str | None:
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
        out = (p.stdout or "").strip()
        return out if out else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    except Exception:
        return None


def _win_to_wsl_path(win_path: str) -> Path | None:
    win_path = (win_path or "").strip()
    if not win_path:
        return None

    # Prefer wslpath if available (handles UNC, spaces, etc.)
    out = _run_capture(["wslpath", "-u", win_path])
    if out:
        return Path(out)

    # Fallback for simple "C:\Users\Name" paths
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", win_path)
    if not m:
        return None
    drive = m.group(1).lower()
    rest = m.group(2).replace("\\", "/")
    return Path(f"/mnt/{drive}/{rest}")


def _find_windows_userprofile() -> Path | None:
    # Try cmd.exe (WSL interop)
    out = _run_capture(["cmd.exe", "/c", "echo", "%USERPROFILE%"])
    if not out:
        out = _run_capture(["/mnt/c/Windows/System32/cmd.exe", "/c", "echo", "%USERPROFILE%"])
    if not out:
        return None
    return _win_to_wsl_path(out)


def _find_windows_codex_home() -> Path | None:
    # Manual override (can be Windows path or already a /mnt/* path)
    override = os.environ.get("CODEX_WIN_HOME")
    if override:
        p = Path(override).expanduser()
        if str(p).startswith(("\\", "//")):
            # UNC path isn't usable from WSL without extra setup
            return None
        if p.exists():
            return p
        # If override looks like Windows path, convert
        wslp = _win_to_wsl_path(override)
        if wslp and wslp.exists():
            return wslp

    userprofile = _find_windows_userprofile()
    if userprofile:
        p = userprofile / ".codex"
        if p.exists():
            return p

    # Fallback: scan common location
    base = Path("/mnt/c/Users")
    if base.exists():
        candidates: list[Path] = []
        try:
            for d in base.iterdir():
                if not d.is_dir():
                    continue
                cand = d / ".codex"
                if (cand / "auth.json").exists():
                    candidates.append(cand)
        except Exception:
            candidates = []

        if candidates:
            # Choose the one with the newest auth.json
            candidates.sort(key=lambda p: (p / "auth.json").stat().st_mtime, reverse=True)
            return candidates[0]

    return None


def _sync_wsl_windows_auth() -> str | None:
    """
    Returns a short status note if any action was taken (for UI/status bar).
    """
    if not _is_wsl():
        return None
    if os.environ.get("CODEX_HOME"):
        # User explicitly controls where Codex reads auth.json from.
        return None

    mode = (os.environ.get("CODEX_WSL_AUTOSYNC") or "bootstrap").strip().lower()
    if mode in {"0", "false", "off", "no"}:
        return None

    global WINDOWS_CODEX_HOME
    WINDOWS_CODEX_HOME = _find_windows_codex_home()
    if not WINDOWS_CODEX_HOME:
        return None

    win_auth = WINDOWS_CODEX_HOME / "auth.json"
    wsl_auth = AUTH_PATH

    if mode == "bootstrap":
        if wsl_auth.exists():
            return None
        if not win_auth.exists():
            return None
        _safe_mkdir(CODEX_HOME)
        _copy_file_private(win_auth, wsl_auth)
        return f"WSL: импортировал auth.json из Windows: {win_auth}"

    if mode == "newest":
        if not win_auth.exists() and not wsl_auth.exists():
            return None
        if win_auth.exists() and not wsl_auth.exists():
            _safe_mkdir(CODEX_HOME)
            _copy_file_private(win_auth, wsl_auth)
            return f"WSL: импортировал auth.json из Windows: {win_auth}"
        if wsl_auth.exists() and not win_auth.exists():
            _copy_file_private(wsl_auth, win_auth)
            return f"WSL: экспортировал auth.json в Windows: {win_auth}"

        # Both exist: copy the newer one over the older
        try:
            win_m = win_auth.stat().st_mtime
            wsl_m = wsl_auth.stat().st_mtime
        except Exception:
            return None

        if abs(win_m - wsl_m) < 1.0:
            return None

        if win_m > wsl_m:
            _safe_mkdir(CODEX_HOME)
            _copy_file_private(win_auth, wsl_auth)
            return f"WSL: обновил auth.json из Windows (новее): {win_auth}"
        else:
            _copy_file_private(wsl_auth, win_auth)
            return f"WSL: обновил auth.json в Windows (новее в WSL): {win_auth}"

    return None


@dataclass(frozen=True)
class AuthInfo:
    account_id: str | None
    email: str | None
    login_type: str | None


@dataclass(frozen=True)
class ProfileInfo:
    name: str
    directory: Path
    auth_path: Path
    meta_path: Path
    auth: AuthInfo


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_mkdir(path: Path) -> None:
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True)
    if existed:
        return
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _write_text_private(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _copy_file_private(src: Path, dst: Path) -> None:
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.write_bytes(src.read_bytes())
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, dst)
    try:
        os.chmod(dst, 0o600)
    except OSError:
        pass


def _decode_jwt_payload(token: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload_b64 = parts[1]
    try:
        payload_b64 += "=" * (-len(payload_b64) % 4)
        raw = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        obj = json.loads(raw.decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _extract_auth_info(auth: dict[str, Any]) -> AuthInfo:
    tokens = auth.get("tokens") if isinstance(auth, dict) else None
    account_id = None
    email = None
    id_token = None

    if isinstance(tokens, dict):
        v = tokens.get("account_id")
        if isinstance(v, str) and v:
            account_id = v
        v = tokens.get("id_token")
        if isinstance(v, str) and v:
            id_token = v

    if id_token:
        payload = _decode_jwt_payload(id_token)
        if isinstance(payload, dict):
            v = payload.get("email") or payload.get("preferred_username")
            if isinstance(v, str) and v:
                email = v

    login_type = None
    api_key = auth.get("OPENAI_API_KEY") if isinstance(auth, dict) else None
    if isinstance(api_key, str) and api_key.strip():
        login_type = "api_key"
    elif isinstance(tokens, dict) and isinstance(tokens.get("refresh_token"), str) and tokens.get("refresh_token"):
        login_type = "chatgpt"

    return AuthInfo(account_id=account_id, email=email, login_type=login_type)


def read_current_auth_info() -> AuthInfo:
    if not AUTH_PATH.exists():
        return AuthInfo(account_id=None, email=None, login_type=None)
    try:
        auth = _load_json(AUTH_PATH)
        if isinstance(auth, dict):
            return _extract_auth_info(auth)
    except Exception:
        pass
    return AuthInfo(account_id=None, email=None, login_type=None)


def _validate_profile_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("Имя профиля пустое.")
    if name in {".", ".."}:
        raise ValueError("Некорректное имя профиля.")
    if any(sep in name for sep in ("/", "\\")):
        raise ValueError("Имя профиля не должно содержать / или \\.")
    if os.name == "nt":
        invalid = '<>:"/\\|?*'
        if any(ch in invalid for ch in name):
            raise ValueError(f"Имя профиля не должно содержать символы: {invalid}")
        if name.endswith((" ", ".")):
            raise ValueError("Имя профиля не должно оканчиваться на пробел или точку.")
    return name


def list_profiles() -> list[ProfileInfo]:
    if not PROFILES_DIR.exists():
        return []

    profiles: list[ProfileInfo] = []
    for entry in sorted(PROFILES_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("_"):
            continue

        auth_path = entry / "auth.json"
        meta_path = entry / "meta.json"
        if not auth_path.exists():
            continue

        auth_info = AuthInfo(account_id=None, email=None, login_type=None)
        try:
            auth = _load_json(auth_path)
            if isinstance(auth, dict):
                auth_info = _extract_auth_info(auth)
        except Exception:
            pass

        profiles.append(
            ProfileInfo(
                name=entry.name,
                directory=entry,
                auth_path=auth_path,
                meta_path=meta_path,
                auth=auth_info,
            )
        )

    return profiles


def save_current_as_profile(name: str, *, overwrite: bool = False) -> ProfileInfo:
    name = _validate_profile_name(name)
    if not AUTH_PATH.exists():
        raise FileNotFoundError(f"Не найден файл авторизации: {AUTH_PATH}")

    _safe_mkdir(PROFILES_DIR)
    profile_dir = PROFILES_DIR / name
    if profile_dir.exists() and not overwrite:
        raise FileExistsError(f"Профиль уже существует: {name}")

    _safe_mkdir(profile_dir)
    auth_dst = profile_dir / "auth.json"
    _copy_file_private(AUTH_PATH, auth_dst)

    auth_info = read_auth_info_from_path(auth_dst)
    meta = {
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "account_id": auth_info.account_id,
        "email": auth_info.email,
        "login_type": auth_info.login_type,
    }
    meta_path = profile_dir / "meta.json"
    _write_text_private(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))

    return ProfileInfo(
        name=name,
        directory=profile_dir,
        auth_path=auth_dst,
        meta_path=meta_path,
        auth=auth_info,
    )


def read_auth_info_from_path(path: Path) -> AuthInfo:
    auth = _load_json(path)
    if not isinstance(auth, dict):
        return AuthInfo(account_id=None, email=None, login_type=None)
    return _extract_auth_info(auth)


def _backup_current_auth() -> Path | None:
    if not AUTH_PATH.exists():
        return None
    _safe_mkdir(BACKUPS_DIR)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = BACKUPS_DIR / f"auth_{ts}.json"
    shutil.copy2(AUTH_PATH, backup_path)
    try:
        os.chmod(backup_path, 0o600)
    except OSError:
        pass
    return backup_path


def switch_to_profile(profile: ProfileInfo) -> None:
    if not profile.auth_path.exists():
        raise FileNotFoundError(f"Не найден файл профиля: {profile.auth_path}")
    _safe_mkdir(CODEX_HOME)
    _backup_current_auth()
    _copy_file_private(profile.auth_path, AUTH_PATH)


def delete_profile(name: str) -> None:
    name = _validate_profile_name(name)
    profile_dir = PROFILES_DIR / name
    if not profile_dir.exists():
        raise FileNotFoundError(f"Профиль не найден: {name}")
    shutil.rmtree(profile_dir)


def _short(s: str | None, *, keep_start: int = 8, keep_end: int = 4) -> str | None:
    if not s:
        return None
    if len(s) <= keep_start + keep_end + 3:
        return s
    return f"{s[:keep_start]}…{s[-keep_end:]}"


class MainWindow(QMainWindow):
    def __init__(self, *, bootstrap_note: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Codex Account Switcher")
        self.setMinimumWidth(560)

        root = QWidget()
        layout = QVBoxLayout(root)

        current_box = QGroupBox("Текущий аккаунт")
        current_layout = QVBoxLayout(current_box)
        self.current_label = QLabel()
        self.current_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.codex_home_label = QLabel(f"CODEX_HOME: {CODEX_HOME}")
        self.codex_home_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.win_codex_home_label: QLabel | None = None
        if _is_wsl():
            win = str(WINDOWS_CODEX_HOME) if WINDOWS_CODEX_HOME else "(не найден)"
            self.win_codex_home_label = QLabel(f"Windows CODEX_HOME: {win}")
            self.win_codex_home_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.save_btn = QPushButton("Сохранить текущий как профиль…")
        self.save_btn.clicked.connect(self.on_save_current)
        current_layout.addWidget(self.current_label)
        current_layout.addWidget(self.codex_home_label)
        if self.win_codex_home_label is not None:
            current_layout.addWidget(self.win_codex_home_label)
        current_layout.addWidget(self.save_btn)

        profiles_box = QGroupBox("Профили")
        profiles_layout = QVBoxLayout(profiles_box)
        self.profiles_list = QListWidget()
        profiles_layout.addWidget(self.profiles_list)

        actions = QHBoxLayout()
        self.switch_btn = QPushButton("Переключить")
        self.switch_btn.clicked.connect(self.on_switch)
        self.delete_btn = QPushButton("Удалить")
        self.delete_btn.clicked.connect(self.on_delete)
        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.clicked.connect(self.refresh)
        self.open_dir_btn = QPushButton("Открыть папку профилей")
        self.open_dir_btn.clicked.connect(self.on_open_profiles_dir)
        actions.addWidget(self.switch_btn)
        actions.addWidget(self.delete_btn)
        actions.addWidget(self.refresh_btn)
        actions.addWidget(self.open_dir_btn)
        profiles_layout.addLayout(actions)

        info = QLabel("После переключения просто запусти новый `codex` (старые процессы не переключатся сами).")
        info.setWordWrap(True)

        layout.addWidget(current_box)
        layout.addWidget(profiles_box)
        layout.addWidget(info)

        self.setCentralWidget(root)
        if bootstrap_note:
            self.statusBar().showMessage(bootstrap_note)
        else:
            self.statusBar().showMessage("Готово.")
        self.refresh()

    def refresh(self) -> None:
        current = read_current_auth_info()
        if not AUTH_PATH.exists():
            self.current_label.setText(f"auth.json не найден: {AUTH_PATH} (сначала сделай `codex login`).")
        else:
            parts: list[str] = []
            if current.email:
                parts.append(current.email)
            if current.account_id:
                parts.append(f"account_id={_short(current.account_id)}")
            if current.login_type:
                parts.append(f"type={current.login_type}")
            self.current_label.setText("Текущий: " + (" | ".join(parts) if parts else "не удалось определить"))

        self.profiles_list.clear()
        profiles = list_profiles()
        for p in profiles:
            label_parts = [p.name]
            if p.auth.email:
                label_parts.append(p.auth.email)
            elif p.auth.account_id:
                label_parts.append(f"account_id={_short(p.auth.account_id)}")
            if p.auth.login_type:
                label_parts.append(p.auth.login_type)
            item = QListWidgetItem(" — ".join(label_parts))
            item.setData(Qt.UserRole, p.name)
            if current.account_id and p.auth.account_id and current.account_id == p.auth.account_id:
                item.setText("✓ " + item.text())
            self.profiles_list.addItem(item)

        self.statusBar().showMessage(f"Профилей: {len(profiles)}")

    def selected_profile(self) -> ProfileInfo | None:
        item = self.profiles_list.currentItem()
        if item is None:
            return None
        name = item.data(Qt.UserRole)
        if not isinstance(name, str):
            return None
        for p in list_profiles():
            if p.name == name:
                return p
        return None

    def on_save_current(self) -> None:
        if not AUTH_PATH.exists():
            QMessageBox.warning(self, "Codex", f"Не найден {AUTH_PATH}\nСначала сделай `codex login`.")
            return

        name, ok = QInputDialog.getText(self, "Сохранить профиль", "Имя профиля:", text="")
        if not ok:
            return
        try:
            name = _validate_profile_name(name)
        except ValueError as e:
            QMessageBox.warning(self, "Codex", str(e))
            return

        overwrite = False
        if (PROFILES_DIR / name).exists():
            r = QMessageBox.question(
                self,
                "Перезаписать?",
                f"Профиль '{name}' уже существует. Перезаписать?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if r != QMessageBox.StandardButton.Yes:
                return
            overwrite = True

        try:
            save_current_as_profile(name, overwrite=overwrite)
        except Exception as e:
            QMessageBox.critical(self, "Codex", f"Не удалось сохранить профиль:\n{e}")
            return

        self.refresh()
        self.statusBar().showMessage(f"Сохранено: {name}")

    def on_switch(self) -> None:
        profile = self.selected_profile()
        if profile is None:
            QMessageBox.information(self, "Codex", "Выбери профиль в списке.")
            return

        r = QMessageBox.question(
            self,
            "Переключить аккаунт?",
            f"Переключить Codex на профиль '{profile.name}'?\n\nБудет перезаписан файл:\n{AUTH_PATH}\n\n"
            f"Текущий auth.json будет сохранён в бэкап:\n{BACKUPS_DIR}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return

        try:
            switch_to_profile(profile)
        except Exception as e:
            QMessageBox.critical(self, "Codex", f"Не удалось переключить:\n{e}")
            return

        self.refresh()
        self.statusBar().showMessage(f"Переключено: {profile.name}")

    def on_delete(self) -> None:
        profile = self.selected_profile()
        if profile is None:
            QMessageBox.information(self, "Codex", "Выбери профиль в списке.")
            return

        r = QMessageBox.question(
            self,
            "Удалить профиль?",
            f"Удалить профиль '{profile.name}'?\n\nЭто удалит папку:\n{profile.directory}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return

        try:
            delete_profile(profile.name)
        except Exception as e:
            QMessageBox.critical(self, "Codex", f"Не удалось удалить:\n{e}")
            return

        self.refresh()
        self.statusBar().showMessage(f"Удалено: {profile.name}")

    def on_open_profiles_dir(self) -> None:
        _safe_mkdir(PROFILES_DIR)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(PROFILES_DIR)))


def main() -> int:
    bootstrap_note = _sync_wsl_windows_auth()
    app = QApplication(sys.argv)
    window = MainWindow(bootstrap_note=bootstrap_note)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

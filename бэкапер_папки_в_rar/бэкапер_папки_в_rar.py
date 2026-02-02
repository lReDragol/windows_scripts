#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GUI-утилита: бэкап выбранной папки в RAR (через rar.exe/WinRAR).

Требования:
- установлен WinRAR/RAR и команда `rar` доступна в PATH
- pip install PySide6

Скрипт создаёт (или находит) папку !backup в корне проекта и кладёт туда архив.
"""

import os
import sys
import subprocess
import re
import json
from pathlib import Path
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QFileDialog, QTabWidget, QScrollArea, QCheckBox,
    QLabel, QGroupBox, QComboBox, QSpinBox, QMessageBox,
    QPlainTextEdit
)
from PySide6.QtCore import Qt

# ---------- Константы ----------

# Имя папки для бэкапов — '!' в начале, чтобы была сверху при сортировке
BACKUP_DIR_NAME = "!backup"

DEFAULT_EXTENSIONS = [
    ".py", ".txt", ".md",
    ".png", ".jpg", ".jpeg",
    ".json", ".yml", ".yaml",
]

# Это ПАТТЕРНЫ, а не только папки
DEFAULT_EXCLUDE_PATTERNS = [
    ".idea",
    ".venv",
    "__pycache__",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
]

# Путь к конфигу: %APPDATA%/drago/pythontools/backup/config.json
CONFIG_REL_PATH = Path("drago") / "pythontools" / "backup" / "config.json"


# ---------- Вспомогательные функции ----------

def get_config_path() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        base = Path(appdata)
    else:
        # На всякий случай под *nix
        base = Path.home() / ".config"
    cfg_path = base / CONFIG_REL_PATH
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    return cfg_path


def normalize_backup_name(name: str) -> str:
    """
    Нормализуем имя папки для поиска backup-папки.
    Убираем невидимые/служебные символы и приводим к нижнему регистру.
    """
    return re.sub(r"[\u200b\s!_\-]+", "", name.lower())


def get_or_create_backup_dir(project_root: str) -> str:
    """
    Ищем или создаём папку для бэкапов в корне проекта.

    Поддерживаем:
      - старые папки вида '\u200bbackup'
      - обычные 'backup'
      - новые '!backup'
    """
    for entry in os.listdir(project_root):
        full_path = os.path.join(project_root, entry)
        if not os.path.isdir(full_path):
            continue

        norm = normalize_backup_name(entry)
        if norm == "backup" or norm.endswith("backup"):
            # если старая папка с нулевым пробелом — переименуем в новое имя
            if "\u200b" in entry and entry != BACKUP_DIR_NAME:
                try:
                    new_path = os.path.join(project_root, BACKUP_DIR_NAME)
                    if not os.path.exists(new_path):
                        os.rename(full_path, new_path)
                        full_path = new_path
                except OSError:
                    # если не удалось — пользуемся как есть
                    pass
            return full_path

    # Папка не найдена — создаём новую
    candidate = BACKUP_DIR_NAME
    index = 1
    while os.path.exists(os.path.join(project_root, candidate)):
        candidate = f"{BACKUP_DIR_NAME}_{index}"
        index += 1

    full = os.path.join(project_root, candidate)
    os.makedirs(full, exist_ok=True)
    return full


def collect_files_for_backup(
    project_root: str,
    include_exts: set[str],
    exclude_patterns: set[str],
    size_mode: str,
    size_limit_bytes: int,
) -> list[str]:
    """
    Собираем список файлов для бэкапа по настройкам.
    size_mode: "none" | "max" | "min"

    exclude_patterns — универсальный список ПАТТЕРНОВ (регистр игнорируется).

    Примеры строк в поле "Исключения":

      .idea        -> исключит всё, где в пути есть ".idea"
      __pycache__  -> все каталоги/пути с "__pycache__"
      __init__.py  -> все файлы __init__.py во всех подпапках
      logs         -> любые пути, содержащие "logs"
    """
    files: list[str] = []

    # нормализуем список исключений: в нижний регистр, без пробелов
    patterns = {p.strip().lower() for p in exclude_patterns if p.strip()}

    project_root = os.path.abspath(project_root)

    for root, dirs, filenames in os.walk(project_root):
        root_abs = os.path.abspath(root)

        # Фильтруем каталоги
        new_dirs = []
        for d in dirs:
            d_lower = d.lower()

            # Не заходим в папку бэкапов
            if normalize_backup_name(d) == "backup":
                continue

            # Не заходим в папки, чьё имя совпало с каким-либо паттерном
            if any(p in d_lower for p in patterns):
                continue

            new_dirs.append(d)
        dirs[:] = new_dirs

        for fname in filenames:
            full_path = os.path.join(root, fname)

            # относительный путь относительно корня проекта
            rel_path = os.path.relpath(full_path, project_root)
            rel_norm = rel_path.replace(os.sep, "/")
            rel_lower = rel_norm.lower()
            base_lower = fname.lower()

            # ---- фильтр по паттернам в пути/имени ----
            if any(p in base_lower or p in rel_lower for p in patterns):
                continue

            ext = os.path.splitext(fname)[1].lower()

            # Фильтр по расширениям
            if include_exts and ext not in include_exts:
                continue

            # Фильтр по размеру
            if size_mode != "none":
                try:
                    size = os.path.getsize(full_path)
                except OSError:
                    continue

                if size_mode == "max" and size > size_limit_bytes:
                    continue
                if size_mode == "min" and size < size_limit_bytes:
                    continue

            files.append(full_path)

    return files


def build_archive_name(backup_dir: str, include_time: bool) -> str:
    """
    Строим имя архива с датой/временем и версией.

    Формат:
      с временем:   HH-MM-SS_DD-MM-YYYY.rar
      без времени:  DD-MM-YYYY.rar
      при совпадении: ..._v1.rar, ..._v2.rar и т.д.
    """
    now = datetime.now()
    date_str = now.strftime("%d-%m-%Y")

    if include_time:
        time_str = now.strftime("%H-%M-%S")
        base = f"{time_str}_{date_str}"
    else:
        base = date_str

    existing = [
        f for f in os.listdir(backup_dir)
        if f.startswith(base) and f.lower().endswith(".rar")
    ]

    if not existing:
        return os.path.join(backup_dir, base + ".rar")

    max_v = 0
    pattern = re.compile(re.escape(base) + r"_v(\d+)\.rar$", re.IGNORECASE)
    for fname in existing:
        m = pattern.match(fname)
        if m:
            try:
                num = int(m.group(1))
                if num > max_v:
                    max_v = num
            except ValueError:
                pass

    new_v = max_v + 1 if max_v > 0 else 1
    return os.path.join(backup_dir, f"{base}_v{new_v}.rar")


def create_rar_archive(
    project_root: str,
    backup_dir: str,
    files: list[str],
    include_time: bool,
    keep_root_dir: bool = True,
) -> tuple[str, str]:
    """
    Создаёт RAR-архив через внешнюю программу `rar`.
    Возвращает (путь_к_архиву, stdout_ rar).

    keep_root_dir = True:
        внутри архива будет папка с именем проекта, например:
        ai_trader/cli/dataset.py

    Требуется установленный rar.exe / WinRAR в PATH.
    """
    archive_path = build_archive_name(backup_dir, include_time)

    project_root = os.path.abspath(project_root)
    backup_dir = os.path.abspath(backup_dir)

    if keep_root_dir:
        # rar запускаем из родительской папки проекта
        parent_dir = os.path.dirname(project_root)
        project_name = os.path.basename(project_root.rstrip(os.sep))

        # путь к архиву относительно родителя
        archive_rel_path = os.path.relpath(archive_path, parent_dir)

        # файлы с префиксом имени проекта
        rel_files = [
            os.path.join(project_name, os.path.relpath(f, project_root))
            for f in files
        ]
        cmd_cwd = parent_dir
    else:
        # архив создаём по абсолютному пути, файлы относительно project_root
        archive_rel_path = archive_path
        rel_files = [os.path.relpath(f, project_root) for f in files]
        cmd_cwd = project_root

    cmd = ["rar", "a", archive_rel_path] + rel_files

    result = subprocess.run(
        cmd,
        cwd=cmd_cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"rar завершился с кодом {result.returncode}.\n\nВывод:\n{result.stdout}"
        )

    # Доп. проверка — действительно ли файл появился
    if not os.path.exists(archive_path):
        raise RuntimeError(
            "rar отработал без ошибок, но архив не найден по пути:\n"
            f"{archive_path}\n\nПолный вывод rar:\n{result.stdout}"
        )

    return archive_path, result.stdout


# ---------- Главное окно ----------

class BackupWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Проектный бэкап (RAR, PySide6)")
        self.resize(900, 600)

        self.ext_checkboxes: dict[str, QCheckBox] = {}
        self.config_path: Path = get_config_path()

        self._init_ui()
        self.load_settings()

    # ----- UI -----

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)

        # Выбор папки проекта
        path_layout = QHBoxLayout()
        self.project_edit = QLineEdit()
        self.project_edit.setPlaceholderText("Путь к папке проекта...")
        browse_btn = QPushButton("Обзор...")

        browse_btn.clicked.connect(self.on_browse_clicked)

        path_layout.addWidget(self.project_edit)
        path_layout.addWidget(browse_btn)
        main_layout.addLayout(path_layout)

        # Вкладки
        self.tabs = QTabWidget()
        self._init_extensions_tab()
        self._init_excludes_tab()
        self._init_settings_tab()
        main_layout.addWidget(self.tabs)

        # Кнопка запуска
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)

        self.backup_btn = QPushButton("Создать бэкап")
        self.backup_btn.clicked.connect(self.on_backup_clicked)

        btn_layout.addWidget(self.backup_btn)
        main_layout.addLayout(btn_layout)

        # Лог
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setPlaceholderText("Лог выполнения будет показан здесь...")
        main_layout.addWidget(self.log_edit)

    def _init_extensions_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        info_label = QLabel("Выбери типы файлов, которые будут попадать в бэкап:")
        layout.addWidget(info_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)

        # Чекбоксы для расширений по умолчанию
        for ext in DEFAULT_EXTENSIONS:
            cb = QCheckBox(ext)
            cb.setChecked(True)
            self.ext_checkboxes[ext] = cb
            inner_layout.addWidget(cb)

        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll)

        # Добавление кастомного расширения
        add_layout = QHBoxLayout()
        self.custom_ext_edit = QLineEdit()
        self.custom_ext_edit.setPlaceholderText("Например: .csv")
        add_btn = QPushButton("Добавить расширение")

        add_btn.clicked.connect(self.on_add_extension_clicked)

        add_layout.addWidget(self.custom_ext_edit)
        add_layout.addWidget(add_btn)
        layout.addLayout(add_layout)

        self.tabs.addTab(tab, "Типы файлов")

    def _init_excludes_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        group = QGroupBox("Что НЕ сохранять в бэкапе (имена/паттерны)")
        group_layout = QVBoxLayout(group)

        label = QLabel(
            "Каждую строку воспринимаю как ПАТТЕРН (без учёта регистра).\n"
            "Примеры:\n"
            "  .idea        -> исключит .idea\n"
            "  __pycache__  -> исключит все __pycache__\n"
            "  __init__.py  -> исключит все файлы __init__.py\n"
            "  logs         -> исключит всё, где путь содержит 'logs'\n"
        )
        group_layout.addWidget(label)

        self.exclude_patterns_edit = QPlainTextEdit()
        self.exclude_patterns_edit.setPlaceholderText(
            "Пример:\n.idea\n.venv\n__pycache__\n__init__.py"
        )
        self.exclude_patterns_edit.setPlainText("\n".join(DEFAULT_EXCLUDE_PATTERNS))
        group_layout.addWidget(self.exclude_patterns_edit)

        layout.addWidget(group)

        # Ограничение по размеру
        size_group = QGroupBox("Правило по размеру файла")
        size_layout = QHBoxLayout(size_group)

        self.size_mode_combo = QComboBox()
        self.size_mode_combo.addItems([
            "Без ограничения",
            "Не больше (МБ)",
            "Не меньше (МБ)",
        ])

        self.size_spin = QSpinBox()
        self.size_spin.setRange(1, 10240)
        self.size_spin.setValue(50)
        self.size_spin.setSuffix(" МБ")

        size_layout.addWidget(QLabel("Режим:"))
        size_layout.addWidget(self.size_mode_combo)
        size_layout.addWidget(self.size_spin)
        size_layout.addStretch(1)

        layout.addWidget(size_group)
        layout.addStretch(1)

        self.tabs.addTab(tab, "Исключения")

    def _init_settings_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        cfg_label = QLabel(f"Файл настроек: {self.config_path}")
        cfg_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(cfg_label)

        self.include_time_checkbox = QCheckBox(
            "Добавлять время в имя архива (HH-MM-SS)"
        )
        self.include_time_checkbox.setChecked(True)
        layout.addWidget(self.include_time_checkbox)

        self.keep_root_dir_checkbox = QCheckBox(
            "Сохранять корневую папку проекта внутри архива"
        )
        self.keep_root_dir_checkbox.setChecked(True)
        layout.addWidget(self.keep_root_dir_checkbox)

        btn_layout = QHBoxLayout()
        save_btn = QPushButton("Сохранить настройки сейчас")
        reset_btn = QPushButton("Сбросить настройки по умолчанию")

        save_btn.clicked.connect(self.on_save_settings_clicked)
        reset_btn.clicked.connect(self.on_reset_settings_clicked)

        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(reset_btn)
        btn_layout.addStretch(1)
        layout.addLayout(btn_layout)

        layout.addStretch(1)
        self.tabs.addTab(tab, "Настройки")

    # ----- Лог / события окна -----

    def log(self, text: str):
        self.log_edit.appendPlainText(text)

    def closeEvent(self, event):
        # Автосохранение настроек при закрытии
        try:
            self.save_settings()
        except Exception as e:
            print("Не удалось сохранить настройки при закрытии:", e)
        super().closeEvent(event)

    # ----- Handlers -----

    def on_browse_clicked(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Выбор папки проекта",
            ""
        )
        if directory:
            self.project_edit.setText(directory)

    def on_add_extension_clicked(self):
        ext = self.custom_ext_edit.text().strip().lower()
        if not ext:
            return

        if not ext.startswith("."):
            ext = "." + ext

        if ext in self.ext_checkboxes:
            QMessageBox.information(self, "Инфо", f"Расширение {ext} уже есть в списке.")
            return

        cb = QCheckBox(ext)
        cb.setChecked(True)
        self.ext_checkboxes[ext] = cb

        # Добавляем в конец списка чекбоксов во вкладке "Типы файлов"
        ext_tab = self.tabs.widget(0)
        scroll: QScrollArea = ext_tab.findChild(QScrollArea)
        inner = scroll.widget()
        inner_layout: QVBoxLayout = inner.layout()
        inner_layout.insertWidget(inner_layout.count() - 1, cb)

        self.custom_ext_edit.clear()

    def on_backup_clicked(self):
        project_path = self.project_edit.text().strip()

        if not project_path or not os.path.isdir(project_path):
            QMessageBox.critical(self, "Ошибка", "Укажи корректную папку проекта.")
            return

        # Собираем расширения
        include_exts = {
            ext for ext, cb in self.ext_checkboxes.items()
            if cb.isChecked()
        }

        if not include_exts:
            QMessageBox.critical(self, "Ошибка", "Не выбрано ни одного типа файла.")
            return

        # Исключаемые паттерны
        exclude_patterns = {
            line.strip()
            for line in self.exclude_patterns_edit.toPlainText().splitlines()
            if line.strip()
        }

        # Режим размера
        idx = self.size_mode_combo.currentIndex()
        if idx == 0:
            size_mode = "none"
        elif idx == 1:
            size_mode = "max"
        else:
            size_mode = "min"

        size_limit_mb = self.size_spin.value()
        size_limit_bytes = size_limit_mb * 1024 * 1024

        include_time = self.include_time_checkbox.isChecked()
        keep_root_dir = self.keep_root_dir_checkbox.isChecked()

        # Для отладки — смотрим, какие паттерны реально используются
        self.log(f"Исключения (паттерны): {sorted(exclude_patterns)}")

        # Сканируем проект
        self.log("Сканирование проекта...")
        files = collect_files_for_backup(
            project_root=project_path,
            include_exts=include_exts,
            exclude_patterns=exclude_patterns,
            size_mode=size_mode,
            size_limit_bytes=size_limit_bytes,
        )

        if not files:
            QMessageBox.warning(
                self, "Ничего не найдено",
                "По заданным правилам не найдено ни одного файла для бэкапа."
            )
            self.log("Файлы не найдены.")
            return

        self.log(f"Файлов для бэкапа: {len(files)}")

        # Папка бэкапов
        backup_dir = get_or_create_backup_dir(project_path)
        self.log(f"Папка для бэкапов: {backup_dir}")

        # Сохраняем настройки
        self.save_settings()

        # Создаём RAR
        try:
            self.backup_btn.setEnabled(False)
            self.log("Создание RAR-архива (нужен установленный rar.exe / WinRAR в PATH)...")
            archive_path, output = create_rar_archive(
                project_root=project_path,
                backup_dir=backup_dir,
                files=files,
                include_time=include_time,
                keep_root_dir=keep_root_dir,
            )
            self.log(output)
            self.log(f"Готово! Архив: {archive_path}")
            QMessageBox.information(self, "Готово", f"Бэкап создан:\n{archive_path}")
        except FileNotFoundError:
            QMessageBox.critical(
                self,
                "rar не найден",
                "Не удалось запустить 'rar'.\n"
                "Установи WinRAR / RAR и добавь его в PATH, "
                "или положи rar.exe в одну папку со скриптом.",
            )
            self.log("Ошибка: rar.exe не найден.")
        except Exception as e:
            QMessageBox.critical(
                self,
                "Ошибка при создании архива",
                str(e)
            )
            self.log(f"Ошибка: {e}")
        finally:
            self.backup_btn.setEnabled(True)

    # ----- Работа с настройками -----

    def apply_default_settings(self):
        # расширения по умолчанию включены, остальные — выключены
        for ext, cb in self.ext_checkboxes.items():
            cb.setChecked(ext in DEFAULT_EXTENSIONS)

        self.exclude_patterns_edit.setPlainText("\n".join(DEFAULT_EXCLUDE_PATTERNS))
        self.size_mode_combo.setCurrentIndex(0)
        self.size_spin.setValue(50)
        self.include_time_checkbox.setChecked(True)
        self.keep_root_dir_checkbox.setChecked(True)

    def load_settings(self):
        if not self.config_path.exists():
            self.apply_default_settings()
            return

        try:
            with self.config_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.log(f"Не удалось загрузить настройки: {e}")
            self.apply_default_settings()
            return

        # ---------- расширения ----------
        ext_all = data.get("extensions_all")
        if isinstance(ext_all, list) and ext_all:
            # Добавляем недостающие чекбоксы
            for ext in ext_all:
                if not isinstance(ext, str):
                    continue
                e = ext.strip().lower()
                if not e:
                    continue
                if not e.startswith("."):
                    e = "." + e
                if e not in self.ext_checkboxes:
                    cb = QCheckBox(e)
                    cb.setChecked(True)
                    self.ext_checkboxes[e] = cb
                    ext_tab = self.tabs.widget(0)
                    scroll: QScrollArea = ext_tab.findChild(QScrollArea)
                    inner = scroll.widget()
                    inner_layout: QVBoxLayout = inner.layout()
                    inner_layout.insertWidget(inner_layout.count() - 1, cb)

            enabled = data.get("extensions_enabled", [])
            enabled_norm = set()
            for ext in enabled:
                if not isinstance(ext, str):
                    continue
                e = ext.strip().lower()
                if not e:
                    continue
                if not e.startswith("."):
                    e = "." + e
                enabled_norm.add(e)

            if not enabled_norm:
                enabled_norm = set(self.ext_checkboxes.keys())

            for ext, cb in self.ext_checkboxes.items():
                cb.setChecked(ext in enabled_norm)
        else:
            self.apply_default_settings()

        # ---------- исключения (миграция старого формата) ----------
        patterns = None

        if isinstance(data.get("exclude_patterns"), list) and data["exclude_patterns"]:
            patterns = data["exclude_patterns"]
        elif isinstance(data.get("exclude_names"), list) and data["exclude_names"]:
            patterns = data["exclude_names"]
        elif isinstance(data.get("exclude_dirs"), list) and data["exclude_dirs"]:
            patterns = data["exclude_dirs"]

        if isinstance(patterns, list) and patterns:
            self.exclude_patterns_edit.setPlainText("\n".join(patterns))
        else:
            self.exclude_patterns_edit.setPlainText("\n".join(DEFAULT_EXCLUDE_PATTERNS))

        # ---------- размер ----------
        size_mode = data.get("size_mode", "none")
        if size_mode == "max":
            self.size_mode_combo.setCurrentIndex(1)
        elif size_mode == "min":
            self.size_mode_combo.setCurrentIndex(2)
        else:
            self.size_mode_combo.setCurrentIndex(0)

        self.size_spin.setValue(int(data.get("size_limit_mb", 50)))
        self.include_time_checkbox.setChecked(bool(data.get("include_time", True)))
        self.keep_root_dir_checkbox.setChecked(bool(data.get("keep_root_dir", True)))

    def save_settings(self):
        data: dict = {}

        data["extensions_all"] = list(self.ext_checkboxes.keys())
        data["extensions_enabled"] = [
            ext for ext, cb in self.ext_checkboxes.items() if cb.isChecked()
        ]

        patterns = [
            line.strip()
            for line in self.exclude_patterns_edit.toPlainText().splitlines()
            if line.strip()
        ]
        data["exclude_patterns"] = patterns

        idx = self.size_mode_combo.currentIndex()
        if idx == 1:
            size_mode = "max"
        elif idx == 2:
            size_mode = "min"
        else:
            size_mode = "none"
        data["size_mode"] = size_mode
        data["size_limit_mb"] = self.size_spin.value()

        data["include_time"] = self.include_time_checkbox.isChecked()
        data["keep_root_dir"] = self.keep_root_dir_checkbox.isChecked()

        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with self.config_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.log(f"Настройки сохранены: {self.config_path}")
        except Exception as e:
            self.log(f"Не удалось сохранить настройки: {e}")

    def on_save_settings_clicked(self):
        self.save_settings()
        QMessageBox.information(self, "Настройки", "Настройки сохранены.")

    def on_reset_settings_clicked(self):
        try:
            if self.config_path.exists():
                self.config_path.unlink()
        except Exception as e:
            self.log(f"Не удалось удалить файл настроек: {e}")

        self.apply_default_settings()
        self.log("Настройки сброшены к значениям по умолчанию.")
        QMessageBox.information(self, "Настройки", "Сброшено к значениям по умолчанию.")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = BackupWindow()
    window.show()
    sys.exit(app.exec())

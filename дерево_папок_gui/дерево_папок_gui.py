#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Простое приложение на PySide6 для построения дерева каталогов.
Поддерживает исключение папок по маскам, ограничение глубины,
игнорирование скрытых и симлинков.

Запуск:
    python дерево_папок_gui.py

Требования:
    pip install PySide6
"""
import os
import sys
import fnmatch
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QPlainTextEdit, QSizePolicy, QSpinBox,
    QVBoxLayout, QWidget, QFileDialog, QMessageBox
)


TREE_VERT = "│  "
TREE_SPACE = "   "
TREE_T = "┝"      # оставим стиль исходного скрипта
TREE_L = "└"


@dataclass(frozen=True)
class WalkOptions:
    exclude: Sequence[str] = ()
    max_depth: int = 0            # 0 = без ограничений
    ignore_hidden: bool = True
    follow_symlinks: bool = False


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _compile_exclude(patterns: Iterable[str]) -> Set[str]:
    """Подготовка множества масок (без пустых строк), обрезаем пробелы."""
    cleaned = set()
    for p in patterns:
        p = p.strip()
        if p:
            cleaned.add(p)
    return cleaned


def _should_skip_dir(dir_name: str, rel_path: str, masks: Set[str]) -> bool:
    """Проверка, нужно ли пропустить каталог по маскам.
    Совпадение проверяется по имени каталога и по относительному пути.
    """
    if not masks:
        return False
    for pat in masks:
        if fnmatch.fnmatch(dir_name, pat) or fnmatch.fnmatch(rel_path, pat):
            return True
    return False


def build_tree(root: str, options: Optional[WalkOptions] = None) -> List[str]:
    """Строит строки дерева для каталога root с учётом настроек options."""
    if options is None:
        options = WalkOptions()
    exclude_masks = _compile_exclude(options.exclude)

    lines: List[str] = []
    base = os.path.basename(os.path.abspath(root)) or root
    lines.append(base)

    def walk(path: str, prefix: str, depth: int, rel: str):
        try:
            entries = sorted(os.listdir(path))
        except PermissionError:
            lines.append(f"{prefix}{TREE_T} [доступ запрещён]")
            return
        except FileNotFoundError:
            lines.append(f"{prefix}{TREE_T} [не найдено]")
            return

        # Фильтруем элементы
        filtered = []
        for name in entries:
            if options.ignore_hidden and _is_hidden(name):
                continue
            full_path = os.path.join(path, name)

            # Если это каталог, проверим исключения по маскам
            if os.path.isdir(full_path):
                rel_child = os.path.join(rel, name) if rel else name
                if _should_skip_dir(name, rel_child, exclude_masks):
                    continue

            filtered.append(name)

        count = len(filtered)
        for index, name in enumerate(filtered):
            full_path = os.path.join(path, name)
            connector = TREE_L if index == count - 1 else TREE_T
            lines.append(f"{prefix}{connector} {name}")

            # Погружаемся в каталоги
            try:
                is_dir = os.path.isdir(full_path)
                is_link = os.path.islink(full_path)
            except OSError:
                is_dir = False
                is_link = False

            if is_dir:
                if is_link and not options.follow_symlinks:
                    continue
                if options.max_depth and depth >= options.max_depth:
                    continue
                extension = TREE_SPACE if index == count - 1 else TREE_VERT
                rel_next = os.path.join(rel, name) if rel else name
                walk(full_path, prefix + extension, depth + 1, rel_next)

    walk(root, prefix="", depth=1, rel="")
    return lines


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tree • Простое дерево каталогов (PySide6)")
        self.resize(800, 600)
        self._setup_ui()

    def _setup_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)

        # Поля ввода
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Путь к папке…")
        browse_btn = QPushButton("Обзор…")
        browse_btn.clicked.connect(self._choose_dir)

        exclude_lbl = QLabel("Исключить папки (через запятую):")
        self.exclude_edit = QLineEdit()
        self.exclude_edit.setPlaceholderText(".git, __pycache__, node_modules, *.venv*")

        depth_lbl = QLabel("Макс. глубина (0 — без ограничений):")
        self.depth_spin = QSpinBox()
        self.depth_spin.setRange(0, 999)
        self.depth_spin.setValue(0)

        self.chk_hidden = QCheckBox("Игнорировать скрытые .*")
        self.chk_hidden.setChecked(True)

        self.chk_follow_links = QCheckBox("Следовать симлинкам")
        self.chk_follow_links.setChecked(False)

        build_btn = QPushButton("Построить")
        build_btn.clicked.connect(self._build_tree)

        save_btn = QPushButton("Сохранить в файл…")
        save_btn.clicked.connect(self._save_to_file)
        save_btn.setEnabled(True)  # разрешим всегда — сохраняем текущий текст

        # Вывод
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.output.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Layouts
        grid = QGridLayout()
        grid.addWidget(QLabel("Папка:"), 0, 0)
        grid.addWidget(self.path_edit, 0, 1)
        grid.addWidget(browse_btn, 0, 2)

        grid.addWidget(exclude_lbl, 1, 0)
        grid.addWidget(self.exclude_edit, 1, 1, 1, 2)

        grid.addWidget(depth_lbl, 2, 0)
        grid.addWidget(self.depth_spin, 2, 1)

        grid.addWidget(self.chk_hidden, 3, 0, 1, 2)
        grid.addWidget(self.chk_follow_links, 3, 2)

        button_row = QHBoxLayout()
        button_row.addWidget(build_btn)
        button_row.addWidget(save_btn)
        button_row.addStretch()

        vbox = QVBoxLayout(central)
        vbox.addLayout(grid)
        vbox.addLayout(button_row)
        vbox.addWidget(self.output)

    def _choose_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "Выберите папку", os.path.expanduser("~"))
        if directory:
            self.path_edit.setText(directory)

    def _gather_options(self) -> WalkOptions:
        raw = self.exclude_edit.text().strip()
        if raw:
            exclude = [p.strip() for p in raw.replace(";", ",").split(",")]
        else:
            exclude = []
        return WalkOptions(
            exclude=exclude,
            max_depth=self.depth_spin.value(),
            ignore_hidden=self.chk_hidden.isChecked(),
            follow_symlinks=self.chk_follow_links.isChecked(),
        )

    def _build_tree(self):
        root = self.path_edit.text().strip()
        if not root:
            QMessageBox.warning(self, "Нет пути", "Укажите путь к папке.")
            return
        if not os.path.exists(root):
            QMessageBox.critical(self, "Ошибка", f"Путь «{root}» не найден.")
            return

        try:
            lines = build_tree(root, self._gather_options())
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось построить дерево:\n{e}")
            return

        self.output.setPlainText("\n".join(lines))

    def _save_to_file(self):
        text = self.output.toPlainText()
        if not text:
            QMessageBox.information(self, "Пусто", "Нечего сохранять — сперва постройте дерево.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить файл",
            "tree.txt",
            "Текстовые файлы (*.txt);;Все файлы (*)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить файл:\n{e}")
        else:
            QMessageBox.information(self, "Готово", f"Дерево сохранено в:\n{path}")


def main(argv: Sequence[str] | None = None) -> int:
    app = QApplication(list(argv or sys.argv))
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GUI-утилита: очистка комментариев в .py файлах.

Что умеет:
- удалять комментарии (# ...)
- опционально удалять docstring-и
- опционально удалять /*...*/ внутри QSS тройных строк
- делать резервные копии рядом с файлом

Запуск:
    python очистка_комментариев_py.py
"""

import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
import ast
import io
import os
import re
import shutil
from datetime import datetime
import tokenize

# =========================
# НАСТРОЙКИ
# =========================
REMOVE_DOCSTRINGS = True               # удалять docstring-и ("""...""" как первый стейтмент)
REMOVE_QSS_CSS_COMMENTS = True         # удалять /*...*/ в тройных строках, присвоенных *QSS* переменным
REMOVE_EMPTY_COMMENT_LINES = True      # удалять строки, ставшие пустыми после удаления комментариев
MAKE_BACKUP = True                     # делать .bak_YYYYmmdd_HHMMSS рядом с файлом

CODING_RE = re.compile(r"coding[:=]\s*([-\w.]+)")
TRIPLE_RE = re.compile(r'(?is)^([rub]*)("""|\'\'\')(.*?)(\2)$')
CSS_BLOCK_RE = re.compile(r"/\*.*?\*/", re.S)

def _docstring_token_starts(src: str) -> set[tuple[int, int]]:
    """Возвращает set((lineno, col)) начала docstring-ов (module/class/func)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()

    starts: set[tuple[int, int]] = set()

    def maybe_add(body):
        if not body:
            return
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
            and getattr(first, "lineno", None) is not None
            and getattr(first, "col_offset", None) is not None
        ):
            starts.add((first.lineno, first.col_offset))

    # module docstring
    maybe_add(getattr(tree, "body", []))

    # class/func docstrings
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            maybe_add(getattr(node, "body", []))

    return starts

def _qss_string_token_starts(src: str) -> set[tuple[int, int]]:
    """Начала строковых литералов, присвоенных переменным с именем содержащим 'QSS'."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()

    starts: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name) and "QSS" in t.id.upper():
                    starts.add((node.value.lineno, node.value.col_offset))
    return starts

def _strip_css_comments_in_triple_literal(literal: str) -> str:
    """
    Удаляет /*...*/ только если это тройной литерал.
    Не трогаем f-строки.
    """
    if literal[:1].lower() == "f" or literal[:2].lower().startswith(("rf", "fr")):
        return literal
    m = TRIPLE_RE.match(literal)
    if not m:
        return literal
    prefix, quote, body, _ = m.groups()
    body2 = CSS_BLOCK_RE.sub("", body)
    return f"{prefix}{quote}{body2}{quote}"

def _strip_comments_from_source(
    src: str,
    *,
    remove_docstrings: bool,
    remove_qss_css_comments: bool,
    keep_shebang: bool = True,
    keep_encoding_cookie: bool = True,
    remove_empty_comment_lines: bool = True,
) -> str:
    doc_starts = _docstring_token_starts(src) if remove_docstrings else set()
    qss_starts = _qss_string_token_starts(src) if remove_qss_css_comments else set()

    out_tokens: list[tokenize.TokenInfo] = []
    removed_comment_on_line: set[int] = set()

    tokgen = tokenize.generate_tokens(io.StringIO(src).readline)
    for tok in tokgen:
        if tok.type == tokenize.COMMENT:
            line = tok.start[0]
            s = tok.string

            # shebang + coding cookie лучше сохранять
            if (keep_shebang and line == 1 and s.startswith("#!")) or (
                keep_encoding_cookie and line in (1, 2) and CODING_RE.search(s)
            ):
                out_tokens.append(tok)
            else:
                removed_comment_on_line.add(line)
            continue

        if tok.type == tokenize.STRING and tok.start in doc_starts:
            # выкидываем docstring (как отдельный expr stmt)
            continue

        if tok.type == tokenize.STRING and tok.start in qss_starts:
            # чистим /*...*/ внутри тройной строки
            new_lit = _strip_css_comments_in_triple_literal(tok.string)
            if new_lit != tok.string:
                tok = tokenize.TokenInfo(tok.type, new_lit, tok.start, tok.end, tok.line)

        out_tokens.append(tok)

    new_src = tokenize.untokenize(out_tokens)

    # Убираем строки, ставшие пустыми после удаления COMMENT (best-effort)
    if remove_empty_comment_lines and removed_comment_on_line:
        lines = new_src.splitlines(True)
        kept = []
        for i, line in enumerate(lines, 1):
            if i in removed_comment_on_line and line.strip() == "":
                continue
            kept.append(line)
        new_src = "".join(kept)

    return new_src

def _read_text_with_detected_encoding(path: Path) -> tuple[str, str, str, bool]:
    """
    Возвращает: (text, encoding, newline, had_trailing_newline)
    Открываем через tokenize.open -> уважает PEP 263.
    """
    raw = path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    had_trailing_newline = raw.endswith(b"\n")

    with tokenize.open(str(path)) as f:
        text = f.read()
        enc = f.encoding or "utf-8"

    return text, enc, newline, had_trailing_newline

def _write_text_preserve_newline(path: Path, text: str, encoding: str, newline: str, want_trailing_newline: bool) -> None:
    if newline != "\n":
        text = text.replace("\n", newline)
    if want_trailing_newline and not text.endswith(newline):
        text += newline

    with open(path, "w", encoding=encoding, newline="") as f:
        f.write(text)

def _backup(path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak_{ts}")
    shutil.copy2(path, bak)
    return bak

def process_file(path: Path) -> tuple[bool, str]:
    try:
        src, enc, nl, had_nl = _read_text_with_detected_encoding(path)
    except Exception as e:
        return False, f"{path.name}: не смог прочитать ({e})"

    new_src = _strip_comments_from_source(
        src,
        remove_docstrings=REMOVE_DOCSTRINGS,
        remove_qss_css_comments=REMOVE_QSS_CSS_COMMENTS,
        remove_empty_comment_lines=REMOVE_EMPTY_COMMENT_LINES,
    )

    if new_src == src:
        return False, f"{path.name}: изменений нет"

    try:
        if MAKE_BACKUP:
            _backup(path)
        _write_text_preserve_newline(path, new_src, enc, nl, had_nl)
        return True, f"{path.name}: OK"
    except Exception as e:
        return False, f"{path.name}: не смог записать ({e})"

def main():
    root = tk.Tk()
    root.withdraw()

    paths = filedialog.askopenfilenames(
        title="Выберите .py файлы (можно несколько)",
        filetypes=[("Python files", "*.py")],
    )
    if not paths:
        return

    changed = 0
    msgs = []
    for p in paths:
        ok, msg = process_file(Path(p))
        msgs.append(msg)
        if ok:
            changed += 1

    messagebox.showinfo(
        "Готово",
        f"Обработано: {len(paths)}\nИзменено: {changed}\n\n" + "\n".join(msgs),
    )

if __name__ == "__main__":
    main()

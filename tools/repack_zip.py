#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""重打 waveguard.zip：含最新 CSI 表述代码 + h5 前端源码，排除垃圾与敏感文件。"""
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # e:\小有可为\waveguard
ZIP_PATH = os.path.join(os.path.dirname(ROOT), "waveguard.zip")

EXCLUDE_DIRS = {"node_modules", "dist", "__pycache__", ".git", ".venv", "venv", ".cache"}
EXCLUDE_EXTS = {".db", ".sqlite", ".pyc", ".log", ".lnk"}
EXCLUDE_NAMES = {".env"}

count = 0
with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in sorted(filenames):
            if fn in EXCLUDE_NAMES:
                continue
            if os.path.splitext(fn)[1].lower() in EXCLUDE_EXTS:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, os.path.dirname(ROOT))  # 带 waveguard/ 前缀
            zf.write(full, rel.replace(os.sep, "/"))
            count += 1

print(f"PACKED {count} files -> {ZIP_PATH}")
print(f"SIZE: {os.path.getsize(ZIP_PATH) / 1024:.1f} KB")

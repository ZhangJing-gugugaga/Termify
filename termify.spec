# -*- mode: python ; coding: utf-8 -*-
"""
Termify PyInstaller spec file — build a single-folder Windows .exe.

Usage:
    pyinstaller termify.spec --clean --noconfirm

Output: dist/Termify/Termify.exe (single folder, ~80MB)
"""

import sys
import os
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# Collect Flask/Werkzeug internal data files (debug icons, etc.)
flask_datas = collect_data_files('flask', include_py_files=False)

a = Analysis(
    ['termify_launcher.py'],
    pathex=[os.path.abspath('.')],
    binaries=[],
    datas=[
        ('templates/', 'templates'),
        ('static/', 'static'),
    ] + flask_datas,
    hiddenimports=[
        'jinja2.ext',
        'flask',
        'werkzeug',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'pandas', 'scipy', 'PILtk', 'cryptography'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Termify',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon='static/img/icon.ico' if os.path.exists('static/img/icon.ico') else None,
    version='version_info.txt' if os.path.exists('version_info.txt') else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Termify',
)

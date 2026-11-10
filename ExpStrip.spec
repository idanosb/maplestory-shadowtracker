# -*- mode: python ; coding: utf-8 -*-
# Build:  .venv\Scripts\pyinstaller ExpStrip.spec
from PyInstaller.utils.hooks import collect_submodules

hidden = (collect_submodules('winsdk.windows.media.ocr')
          + collect_submodules('winsdk.windows.globalization')
          + collect_submodules('winsdk.windows.graphics.imaging')
          + collect_submodules('winsdk.windows.storage.streams')
          + collect_submodules('winsdk.windows.foundation')
          + ['win32event', 'win32api', 'win32con', 'win32gui'])

a = Analysis(
    ['expstrip/__main__.py'],
    pathex=['.'],
    binaries=[],
    # the whole folder: anchors (incl. anchor_mesos.png) plus exp/, level/, pickup/,
    # potions/, count/, mesos/ and items/ (loot name + suffix) template folders
    datas=[('expstrip/templates', 'expstrip/templates')],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'pytest'],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='ExpStrip',
    icon='assets/tray.ico',
    console=False,
    upx=False,
    runtime_tmpdir=None,
)

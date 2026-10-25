# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：单文件、无控制台、ASCII exe 名。
#
# 仓库布局是「源码在 src/、资源在 assets/ 和 data/」的非标准布局，所以这里
# 用 SPECPATH（本 spec 所在目录 packaging/）反推仓库根，不要依赖调用时的工作目录。
#
# 打包后的资源路径（对应 src/awards_downloader.py 的 _data_search_dirs()）：
#   app.ico              -> _MEIPASS/app.ico
#   awards_index.json    -> _MEIPASS/data/awards_index.json
# 程序会依次在 _MEIPASS、exe 目录及它们的 data/、assets/ 子目录里找，两者都能命中。
#
# 程序本身只用标准库，excludes 是一份「明确不需要」的精简清单；
# 注意不要排除 concurrent（切片并发下载用到 concurrent.futures）。
# 不打包任何浏览器：预览转 PDF 运行时驱动系统自带 Edge/Chrome。

import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")
ASSETS = os.path.join(ROOT, "assets")
DATA = os.path.join(ROOT, "data")

a = Analysis(
    [os.path.join(SRC, "awards_downloader.py")],
    pathex=[SRC],
    binaries=[],
    datas=[
        (os.path.join(ASSETS, "app.ico"), "."),
        (os.path.join(DATA, "awards_index.json"), "data"),
    ],
    hiddenimports=["preview_pdf", "cdp", "ui_kit"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['unittest', 'pydoc', 'doctest', 'lib2to3', 'distutils',
              'setuptools', 'pip', 'test', 'asyncio'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AwardsDownloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(ASSETS, "app.ico")],
    version=os.path.join(ROOT, "packaging", "version_info.txt"),
)

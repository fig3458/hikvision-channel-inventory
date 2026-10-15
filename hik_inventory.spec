# -*- mode: python ; coding: utf-8 -*-
# Slim onefile build for stdlib + tkinter only.

block_cipher = None

# Heavy / unused stdlib & third-party modules to drop from the bundle.
excludes = [
    "numpy",
    "pandas",
    "scipy",
    "PIL",
    "cv2",
    "matplotlib",
    "sklearn",
    "torch",
    "tensorflow",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "setuptools",
    "pkg_resources",
    "distutils",
    "lib2to3",
    "pydoc",
    "doctest",
    "unittest",
    "test",
    "tkinter.test",
    "xmlrpc",
    "multiprocessing.popen_spawn_posix",
    "asyncio.unix_events",
    "curses",
    "dbm",
    "sqlite3",
    "ssl_servers",
]

a = Analysis(
    ["inventory_gui.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Drop leftover bulky pure modules if Analysis still pulled them.
a.pure = [x for x in a.pure if not any(x[0].startswith(p) for p in (
    "unittest",
    "pydoc",
    "doctest",
    "setuptools",
    "pkg_resources",
))]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="HikChannelInventory",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

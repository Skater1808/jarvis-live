# -*- mode: python ; coding: utf-8 -*-
# PyInstaller Spec für J.A.R.V.I.S
# Erzeugt eine einzelne EXE mit allen Abhängigkeiten

block_cipher = None

a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('frontend', 'frontend'),           # Frontend-Dateien (HTML, CSS, JS)
        ('config.example.json', '.'),       # Beispiel-Config als Fallback
    ],
    hiddenimports=[
        'google.genai',
        'websockets',
        'fastapi',
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'httpx',
        'playwright',
        'playwright.sync_api',
        'playwright.async_api',
        'PIL',
        'PIL._imagingtk',
        'PIL._tkinter_finder',
        'sounddevice',
        'numpy',
        'mcp',
        'aiosqlite',
        'browser_tools',
        'screen_capture',
        'memory',
        'quick_notes',
        'mcp_client',
        'wiki_tools',
        'datetime',
        'json',
        'os',
        'sys',
        'time',
        'asyncio',
        'contextlib',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'tkinter',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'wx',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Jarvis',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,           # True = Konsolenfenster für Debugging sichtbar
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.png',      # Optional: Passe Pfad an wenn du ein Icon hast
)

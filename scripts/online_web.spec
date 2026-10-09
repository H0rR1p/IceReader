from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parent
datas = [(str(root / 'dist'), 'dist'), (str(root / 'assets' / 'ymm4-bridge'), 'ymm4-bridge'),
         (str(root / 'resources' / 'grammar'), 'resources/grammar')]
binaries, hiddenimports = [], []
for package in ('sudachidict_core', 'sudachipy'):
    extra = collect_all(package)
    datas += extra[0]; binaries += extra[1]; hiddenimports += extra[2]
a = Analysis([str(root / 'backend' / 'launcher.py')], pathex=[str(root)],
    datas=datas, binaries=binaries, hiddenimports=hiddenimports)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='IceReaderWeb', console=True)
coll = COLLECT(exe, a.binaries, a.datas, name='IceReaderWeb')

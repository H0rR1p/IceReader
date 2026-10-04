from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parent
datas, binaries, hiddenimports = [], [], []
for package in ('authlib', 'httpx'):
    extra = collect_all(package)
    datas += extra[0]; binaries += extra[1]; hiddenimports += extra[2]
a = Analysis([str(root / 'backend' / 'cloud_launcher.py')], pathex=[str(root)],
    datas=datas, binaries=binaries, hiddenimports=hiddenimports)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='IceReaderCloud', console=True)
coll = COLLECT(exe, a.binaries, a.datas, name='IceReaderCloud')

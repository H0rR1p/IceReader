"""Check that the main executable icon comes from the supplied ICO, not Electron."""
import hashlib
from pathlib import Path
import struct
import sys

import pefile


def verify_icon(executable: Path, icon: Path) -> None:
    source = icon.read_bytes()
    reserved, kind, count = struct.unpack_from('<HHH', source)
    if (reserved, kind) != (0, 1) or not count:
        raise ValueError(f'Invalid ICO: {icon}')
    expected = set()
    for index in range(count):
        size, offset = struct.unpack_from('<II', source, 6 + index * 16 + 8)
        expected.add(hashlib.sha256(source[offset:offset + size]).digest())
    with pefile.PE(str(executable), fast_load=True) as binary:
        binary.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
        resources = {entry.id: entry for entry in binary.DIRECTORY_ENTRY_RESOURCE.entries}
        images = {}
        for entry in resources[3].directory.entries:
            data = entry.directory.entries[0].data.struct
            images[entry.id] = hashlib.sha256(binary.get_data(data.OffsetToData, data.Size)).digest()
        group = resources[14].directory.entries[0].directory.entries[0].data.struct
        group_bytes = binary.get_data(group.OffsetToData, group.Size)
        group_count = struct.unpack_from('<H', group_bytes, 4)[0]
        actual = {images[struct.unpack_from('<H', group_bytes, 6 + index * 14 + 12)[0]]
                  for index in range(group_count)}
        if not actual or not actual.issubset(expected):
            raise ValueError(f'EXE icon does not match bingdu.ico: {executable}')
    print(f'Icon verified: {executable}')


if __name__ == '__main__':
    verify_icon(Path(sys.argv[1]), Path(sys.argv[2]))

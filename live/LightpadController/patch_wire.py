"""Bounded selector graphics. Names become four-pixel columns on the host."""
import unicodedata


_ROWS = {
    'A': '010/101/111/101', 'B': '110/111/101/110',
    'C': '011/100/100/011', 'D': '110/101/101/110',
    'E': '111/110/100/111', 'F': '111/110/100/100',
    'G': '011/100/101/011', 'H': '101/111/101/101',
    'I': '111/010/010/111', 'J': '001/001/101/010',
    'K': '101/110/110/101', 'L': '100/100/100/111',
    'M': '101/111/111/101', 'N': '101/111/111/101',
    'O': '010/101/101/010', 'P': '110/101/110/100',
    'Q': '010/101/111/011', 'R': '110/101/110/101',
    'S': '011/100/001/110', 'T': '111/010/010/010',
    'U': '101/101/101/111', 'V': '101/101/101/010',
    'W': '101/111/111/010', 'X': '101/010/010/101',
    'Y': '101/010/010/010', 'Z': '111/001/010/111',
    '0': '111/101/101/111', '1': '010/110/010/111',
    '2': '110/001/010/111', '3': '110/011/001/110',
    '4': '101/101/111/001', '5': '111/110/001/110',
    '6': '011/100/111/111', '7': '111/001/010/010',
    '8': '111/111/101/111', '9': '111/111/001/110',
    ' ': '000/000/000/000', '-': '000/000/111/000',
    '.': '000/000/000/010', '/': '001/001/010/100',
    '[': '110/100/100/110', ']': '011/001/001/011',
    '?': '110/001/010/010',
}


def name_columns(name):
    text = unicodedata.normalize('NFKD', str(name)).encode('ascii', 'ignore').decode().upper()
    columns = []
    for char in text[:24]:
        rows = _ROWS.get(char, _ROWS['?']).split('/')
        columns.extend(sum((row[x] == '1') << y for y, row in enumerate(rows)) for x in range(3))
        columns.append(0)
    return tuple(columns[:96])


def records(snapshot, presentation):
    """Latest immutable records keyed by meaning, never a remembered patch map."""
    sources = tuple(snapshot.get('sources', ()))[:25]
    result = {('meta',): (0, len(sources))}
    for index, source in enumerate(sources):
        colour = source.get('colour', (0, 0, 0))
        owners = int(source.get('owners', 0)) & 255
        result['source', index] = (1, index, *colour, int(bool(source.get('midi'))),
                                  owners & 127, owners >> 7, int(bool(source.get('available', True))))
    for group, mask in enumerate(snapshot.get('assignments', ())[:8]):
        result['assignment', group] = (2, group, *(int(mask) >> shift & 127 for shift in (0, 7, 14, 21)))
    context = presentation.get('context', 127)
    name = presentation.get('held_input', 127)
    flash = presentation.get('flash', 127)
    flash_group = presentation.get('flash_group', 127)
    result['ui',] = (4, context, name, flash, flash_group)
    if 0 <= name < len(sources):
        columns = name_columns(sources[name].get('name', '')) + (16,)
        for offset in range(0, len(columns), 15):
            chunk = columns[offset:offset + 15]
            result['name', name, offset] = (3, name, offset, *chunk, *((16,) * (15 - len(chunk))))
    return result

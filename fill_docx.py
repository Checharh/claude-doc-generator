# fill_docx.py
"""Fill the Agentic AI brochure .docx by matching its 'field key:' tags.

The template marks a few placeholders with [ brackets ], but most are plain
italic guidance prose with no delimiter at all. What every field does have is a
gray 'field key: <name>' tag under its label, so that is what this keys on.

Layout in every table: a label row holding 'field key: <name>', immediately
followed by the row whose first cell holds the value.
"""

import re
import shutil
import zipfile
from xml.etree import ElementTree as ET

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = f'{{{W}}}'
ET.register_namespace('w', W)

DOCUMENT = 'word/document.xml'
FIELD_KEY = re.compile(r'field key:\s*([A-Za-z_0-9]+)')


def _text(el):
    return ''.join(t.text or '' for t in el.iter(NS + 't'))


def _first_cell(row):
    return row.find(NS + 'tc')


def _locate(root):
    """Map each field key to the cell element that holds its value."""
    slots = {}
    for table in root.iter(NS + 'tbl'):
        rows = list(table.findall(NS + 'tr'))
        for i, row in enumerate(rows):
            match = FIELD_KEY.search(_text(row))
            if not match or i + 1 >= len(rows):
                continue
            cell = _first_cell(rows[i + 1])
            if cell is not None:
                slots.setdefault(match.group(1), []).append(cell)
    return slots


def _template_run(cell):
    """Reuse an existing run's formatting, minus the italics of guidance text."""
    for run in cell.iter(NS + 'r'):
        props = run.find(NS + 'rPr')
        if props is None:
            return None
        clone = ET.fromstring(ET.tostring(props))
        for tag in ('i', 'iCs'):
            for node in clone.findall(NS + tag):
                clone.remove(node)
        return clone
    return None


def _write_cell(cell, value):
    """Replace a cell's paragraphs with one paragraph per line of `value`."""
    paragraphs = cell.findall(NS + 'p')
    keep = paragraphs[0]
    para_props = keep.find(NS + 'pPr')
    run_props = _template_run(cell)

    for extra in paragraphs[1:]:
        cell.remove(extra)
    for child in list(keep):
        keep.remove(child)
    if para_props is not None:
        keep.append(para_props)

    lines = str(value).split('\n') or ['']
    index = list(cell).index(keep)
    for offset, line in enumerate(lines):
        para = keep if offset == 0 else ET.SubElement(cell, NS + 'p')
        if offset:
            cell.remove(para)
            index += 1
            cell.insert(index, para)
            if para_props is not None:
                para.append(ET.fromstring(ET.tostring(para_props)))
        run = ET.SubElement(para, NS + 'r')
        if run_props is not None:
            run.append(ET.fromstring(ET.tostring(run_props)))
        node = ET.SubElement(run, NS + 't')
        node.text = line
        node.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')


def field_keys(template):
    """Return each field key with the placeholder text currently in its cell."""
    with zipfile.ZipFile(template) as archive:
        root = ET.fromstring(archive.read(DOCUMENT))
    return {
        key: _text(cells[0]).strip()
        for key, cells in _locate(root).items()
    }


def fill(template, output, values):
    """Write `output` from `template` with `values` applied. Returns a report."""
    with zipfile.ZipFile(template) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}

    root = ET.fromstring(parts[DOCUMENT])
    slots = _locate(root)

    report = {'filled': {}, 'unknown': [], 'untouched': []}
    for key, value in values.items():
        if key not in slots:
            report['unknown'].append(key)
            continue
        if value is None or str(value).strip() == '':
            continue
        for cell in slots[key]:
            _write_cell(cell, value)
        report['filled'][key] = len(slots[key])
    report['untouched'] = sorted(set(slots) - set(report['filled']))

    parts[DOCUMENT] = ET.tostring(root, xml_declaration=True, encoding='UTF-8')
    shutil.copyfile(template, output)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, blob in parts.items():
            archive.writestr(name, blob)
    return report

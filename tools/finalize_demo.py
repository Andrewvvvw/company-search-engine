#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('search_agent', ROOT / 'skills/company-search-agent/scripts/search_agent.py')
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
out = ROOT / 'outputs/search-agent-01'
card = agent.load(out / 'CSA-DEMO-50-20261121.json')
path = out / (card['code'] + '.xlsx')
headers, rows, links = agent.export_table(card)
ns = {'s': agent.NS}
with zipfile.ZipFile(path) as archive:
    parts = {name: archive.read(name) for name in archive.namelist()}
for number in range(1, 6):
    filename = f'xl/worksheets/sheet{number}.xml'
    doc = ET.fromstring(parts[filename])
    filters = doc.find('s:autoFilter', ns)
    if filters is None:
        filters = ET.Element('{' + agent.NS + '}autoFilter', {'ref': 'A1:' + agent.column(len(headers)) + '11'})
        data = doc.find('s:sheetData', ns)
        doc.insert(list(doc).index(data) + 1, filters)
    for old in doc.findall('s:hyperlinks', ns):
        doc.remove(old)
    hyperlinks = ET.Element('{' + agent.NS + '}hyperlinks')
    later = {'printOptions', 'pageMargins', 'pageSetup', 'headerFooter', 'rowBreaks', 'colBreaks', 'drawing', 'legacyDrawing', 'extLst'}
    insertion = next((i for i, child in enumerate(doc) if child.tag.split('}')[-1] in later), len(doc))
    doc.insert(insertion, hyperlinks)
    rels = ET.Element('Relationships', {'xmlns': agent.PKG})
    for ri, link_row in enumerate(links[(number - 1) * 10:number * 10], 2):
        for ci, target in link_row.items():
            rid = f'evidence-{ri}-{ci}'
            ET.SubElement(hyperlinks, '{' + agent.NS + '}hyperlink', {'ref': agent.column(ci + 1) + str(ri), '{' + agent.REL + '}id': rid})
            ET.SubElement(rels, 'Relationship', {'Id': rid, 'Type': agent.REL + '/hyperlink', 'Target': target, 'TargetMode': 'External'})
    parts[filename] = agent.xml_bytes(doc)
    parts[f'xl/worksheets/_rels/sheet{number}.xml.rels'] = agent.xml_bytes(rels)
with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
    for name, content in parts.items():
        archive.writestr(name, content)
result = agent.validate_xlsx(path, card)
agent.append_event(card, 'export', 'final', {'digest': card['derived']['digest'], 'path': str(path), **result})
agent.atomic_json(out / (card['code'] + '.json'), agent.refresh(card))
agent.atomic_json(out / 'demo-validation.json', result)
print(agent.canonical(result))

#!/usr/bin/env python3
import json
from pathlib import Path
import openpyxl

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / 'outputs/search-agent-01'
data = json.loads((out / 'demo-data.json').read_text())
book = openpyxl.load_workbook(out / (data['code'] + '.xlsx'), data_only=False)
assert len(book.worksheets) == 5
count = hyperlinks = 0
for index, sheet in enumerate(book.worksheets):
    assert sheet.max_row == 11 and sheet.max_column == len(data['headers'])
    assert sheet.freeze_panes == 'A2'
    assert sheet.auto_filter.ref == 'A1:AO11'
    values = list(sheet.values)
    assert list(values[0]) == data['headers']
    assert [list(row) for row in values[1:]] == data['rows'][index * 10:(index + 1) * 10]
    for row in sheet.iter_rows():
        for cell in row:
            assert cell.data_type == 's', (sheet.title, cell.coordinate, cell.data_type)
            assert cell.number_format == '@', (sheet.title, cell.coordinate, cell.number_format)
    for ri, expected in enumerate(data['links'][index * 10:(index + 1) * 10], 2):
        assert sheet.cell(ri, 4).value.startswith('+')
        for ci, target in expected.items():
            assert sheet.cell(ri, int(ci) + 1).hyperlink.target == target
            hyperlinks += 1
    count += sheet.max_row - 1
result = {'engine': 'openpyxl ' + openpyxl.__version__, 'mode': 'read-only verification',
          'rows': count, 'sheet_sizes': [s.max_row - 1 for s in book], 'hyperlinks': hyperlinks,
          'external_values_text': True, 'filters': True, 'frozen_headers': True, 'passed': True}
(out / 'independent-workbook-read.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))

import fs from 'node:fs/promises';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const out = process.argv[2];
if (!out) throw new Error('Usage: node build_demo.mjs ABSOLUTE_OUTPUT_DIRECTORY');
const data = JSON.parse(await fs.readFile(`${out}/demo-data.json`, 'utf8'));
const wb = Workbook.create();
for (let start = 0; start < data.rows.length; start += 10) {
  const name = `ДЕМО ${String(start / 10 + 1).padStart(2, '0')}`;
  const sheet = wb.worksheets.add(name);
  sheet.showGridLines = false;
  const rows = [data.headers, ...data.rows.slice(start, start + 10)];
  const range = sheet.getRangeByIndexes(0, 0, rows.length, data.headers.length);
  range.setNumberFormat('@');
  range.values = rows;
  range.format.font.name = 'Arial';
  range.format.font.size = 10;
  range.format.verticalAlignment = 'top';
  range.format.wrapText = true;
  range.format.rowHeight = 52;
  const header = sheet.getRangeByIndexes(0, 0, 1, data.headers.length);
  header.format.fill = '#203864';
  header.format.font.color = '#FFFFFF';
  header.format.font.bold = true;
  header.format.horizontalAlignment = 'center';
  header.format.verticalAlignment = 'center';
  header.format.rowHeight = 48;
  const widths = [32, 16, 36, 24, 30, 22, 44];
  for (let ci = 0; ci < data.headers.length; ci++) {
    const text = data.headers[ci];
    const width = widths[ci] ?? (/цитата|выдержка/.test(text) ? 58 : /источник|доказательство/.test(text) ? 46 : /дата|проверено/.test(text) ? 22 : 30);
    sheet.getRangeByIndexes(0, ci, rows.length, 1).format.columnWidth = width;
  }
  sheet.freezePanes.freezeRows(1);
}
wb.recalculate();
console.log((await wb.inspect({kind:'sheet',include:'id,name',maxChars:1500})).ndjson);
const result = await SpreadsheetFile.exportXlsx(wb);
await result.save(`${out}/${data.code}.xlsx`);
for (let number = 1; number <= 5; number++) {
  const sheetName = `ДЕМО ${String(number).padStart(2, '0')}`;
  const preview = await wb.render({sheetName, range:'A1:G11', scale:1, format:'png'});
  await fs.writeFile(`${out}/preview-${number}.png`, new Uint8Array(await preview.arrayBuffer()));
}
const proof = await wb.render({sheetName:'ДЕМО 01',range:'L1:Q4',scale:1,format:'png'});
await fs.writeFile(`${out}/preview-evidence.png`, new Uint8Array(await proof.arrayBuffer()));

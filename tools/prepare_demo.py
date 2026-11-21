#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'examples'))
from fixtures import request, candidate
spec = importlib.util.spec_from_file_location('search_agent', ROOT / 'skills/company-search-agent/scripts/search_agent.py')
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
out = ROOT / 'outputs/search-agent-01'
out.mkdir(parents=True, exist_ok=True)
req = request()
card = agent.apply_operations(agent.new_card(req, 'CSA-DEMO-50-20261121'), [candidate(n, req) for n in range(1, 51)])
card = agent.review(card, 'synthetic-demonstration')
agent.atomic_json(out / (card['code'] + '.json'), card)
headers, rows, links = agent.export_table(card)
agent.atomic_json(out / 'demo-data.json', {'code': card['code'], 'headers': headers, 'rows': rows, 'links': links})
agent.atomic_json(ROOT / 'examples/almaty-request.json', {**req, 'demo': False,
    'original_request': 'Найди 50 стоматологических клиник Алматы. Нужны адрес, телефон и подтверждённый рабочий email действующего директора. Итог — один Excel, пять листов по десять.',
    'evidence_policy': 'Первичные открытые источники, действующий директор, опубликованный рабочий email прямо связан с человеком'})
second = request(100, 20, named=False)
second.update(demo=False, geography='Минск, Беларусь', industry='Производители мебели',
              original_request='Найди 100 производителей мебели в Минске. Нужны название, сайт и телефон, 5 листов по 20.',
              evidence_policy='Официальные сайты и подтверждённая действующая география/отрасль')
second['fields'] = [{'key': 'name', 'label': 'Название'}, {'key': 'website', 'label': 'Сайт'}, {'key': 'phone', 'label': 'Телефон'}]
agent.atomic_json(ROOT / 'examples/minsk-request.json', second)
print(json.dumps({'demo_rows': len(rows), 'columns': len(headers), 'code': card['code']}, ensure_ascii=False))

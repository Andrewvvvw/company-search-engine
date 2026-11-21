#!/usr/bin/env python3
import importlib.util
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('search_agent', ROOT / 'skills/company-search-agent/scripts/search_agent.py')
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
req = agent.load(ROOT / 'examples/almaty-request.json')
card = agent.new_card(req, 'CSA-ALMATY-FIELD-20261121')
today = date.today().isoformat()

def evidence(value, url, quote=None):
    return {'value': value, 'sources': [{'url': url, 'checked_at': today, 'quote': quote or value}]}

vovk = 'https://www.drvovk.clinic/team'
park = 'https://dentalpark.kz/contacts'
luch = 'https://luch.asia/services/educational-center/'
offer = 'https://www.benefaktadental.kz/publichnaya-oferta'
ops = [
    {'kind': 'candidate', 'entity_id': 'clinic-dr-vovk', 'payload': {
        'state': 'needs_review', 'reason': 'Директор указан; info@clinicvovk.kz опубликован как общий контакт. Именной рабочий email и оператор не подтверждены.',
        'identity': {'brand': 'Clinic Dr. Vovk'},
        'fields': {'name': evidence('Clinic Dr. Vovk', vovk),
                   'person': evidence('Вовк Виктор Евгеньевич', vovk),
                   'role': evidence('Директор, хирург-имплантолог', vovk, 'Директор, хирург-имплантолог'),
                   'email': evidence('info@clinicvovk.kz', vovk),
                   'city': evidence('Алматы', vovk)},
        'contact': {'current_role': False, 'email_kind': 'company', 'person_email_link': False},
    }},
    {'kind': 'candidate', 'entity_id': 'dentalpark', 'payload': {
        'state': 'needs_review', 'reason': 'reception@dentalpark.kz — приёмная. Нет подтверждённого директора и его опубликованного email.',
        'identity': {'brand': 'DENTALPARK'},
        'fields': {'name': evidence('DENTALPARK', park), 'email': evidence('reception@dentalpark.kz', park)},
        'contact': {'current_role': False, 'email_kind': 'reception', 'person_email_link': False},
    }},
    {'kind': 'candidate', 'entity_id': 'triodont', 'payload': {
        'state': 'needs_review', 'reason': 'Именная публикация указывает директора. Нет прямого опубликованного рабочего email и проверки текущей роли на сайте компании.',
        'identity': {'brand': 'TRIODONT'},
        'fields': {'name': evidence('TRIODONT', luch), 'person': evidence('Бочкарёва Ольга Ивановна', luch),
                   'role': evidence('директор', luch, 'директор стоматологии "TRIODONT", г. Алматы, Казахстан.')},
    }},
    {'kind': 'candidate', 'entity_id': 'benefakta-dental-experts', 'payload': {
        'state': 'needs_review', 'reason': 'Поисковый сниппет и открытый PDF различаются: PDF на домене Benefakta называет Dental Experts и другой сайт. Нужна сверка бренда/оператора; email директора не доказан.',
        'identity': {'legal_name': 'ТОО «Dental Experts»'},
        'fields': {'name': evidence('Dental Experts', offer, 'ТОО ««Dental Experts»')},
    }},
    {'kind': 'coverage', 'entity_id': 'field-initial-pass', 'payload': {
        'checked_at': today, 'accepted': 0, 'quota': 50,
        'queries': ['Алматы стоматология директор email руководитель контакты', 'Алматы стоматология директор email',
                    'Бочкарёва Ольга Ивановна email TRIODONT', 'Вовк Виктор Евгеньевич email'],
        'observed_gap': 'Прямая опубликованная связь рабочего email с действующим директором; для части кандидатов также юридический оператор.',
        'not_exhausted': True, 'note': 'Это начальный полевой проход, а не завершённый T4. Поиск остаётся открытым.'}},
]
for n, (url, query) in enumerate([(vovk, 'Clinic Dr Vovk команда'), (park, 'Dentalpark контакты'), (luch, 'Луч спикеры TRIODONT'), (offer, 'Benefakta реквизиты')], 1):
    ops.append({'kind': 'segment', 'entity_id': f'opened-source-{n}', 'payload': {
        'status': 'done', 'query': query, 'cursor': 'Открытая страница прочитана', 'next_action': 'Первичный просмотр завершён; именные контакты требуют отдельной ветки',
        'attempts': 1, 'processed_urls': [url], 'last_error': None}})
for entity, query, action in [
    ('next-official-vovk', 'https://clinicvovk.kz/', 'Открыть действующий официальный сайт, проверить текущую роль и именные контакты Виктора Вовка'),
    ('next-triodont', 'TRIODONT Алматы Бочкарёва официальный сайт контакты', 'Найти официальный сайт TRIODONT, сверить оператора, роль и именной email'),
    ('next-remaining-clinics', 'Алматы стоматологическая клиника директор рабочий email', 'Продолжить районы и именные профессиональные публикации; не засчитывать общие ящики'),
]:
    ops.append({'kind': 'segment', 'entity_id': entity, 'payload': {
        'status': 'pending', 'query': query, 'cursor': 'Ещё не обработано', 'next_action': action, 'attempts': 0, 'processed_urls': [], 'last_error': None}})
card = agent.apply_operations(card, ops)
agent.atomic_json(ROOT / 'outputs/search-agent-01' / (card['code'] + '.json'), card)
print(agent.canonical(card))

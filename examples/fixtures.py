from datetime import date


def request(quota=50, batch_size=10, named=True):
    fields = [('name', 'Название'), ('city', 'Город'), ('address', 'Адрес'), ('phone', 'Телефон')]
    if named:
        fields += [('person', 'ФИО'), ('role', 'Должность'), ('email', 'Рабочий email директора')]
    return {
        'original_request': 'ДЕМОНСТРАЦИЯ: вымышленные клиники Алматы. Не использовать для контактов.',
        'geography': 'Алматы, Казахстан', 'industry': 'Стоматологические клиники',
        'quota': quota, 'batch_size': batch_size, 'fields': [{'key': k, 'label': v} for k, v in fields],
        'named_contact': named, 'allowed_roles': ['директор', 'генеральный директор'] if named else [],
        'uniqueness': 'company', 'evidence_policy': 'Синтетические данные только для демонстрации механики',
        'independent_review_required': False, 'max_evidence_age_days': 30, 'demo': True,
    }


def record(number=1, req=None):
    req = req or request()
    today = date.today().isoformat()
    person = f'Демо Директор {number:03d}'
    values = {'name': f'ДЕМО Клиника {number:03d}', 'city': 'Алматы', 'address': f'Вымышленная улица, {number}',
              'phone': f'+7 (000) 000-{number:04d}', 'person': person, 'role': 'директор',
              'email': f'director{number}@clinic{number}.example'}
    base = f'https://clinic{number}.example'
    def source(suffix, quote):
        return {'url': base + suffix, 'checked_at': today, 'quote': quote}
    fields = {}
    for f in req['fields']:
        key = f['key']
        quote = values.get(key, f'ДЕМО значение {number}')
        if key in {'role', 'email'}:
            quote = person + ' — ' + values[key]
        fields[key] = {'value': values.get(key, f'ДЕМО значение {number}'), 'sources': [source('/' + key, quote)]}
    return {
        'state': 'accepted',
        'identity': {'registration_id': f'DEMO-{number:06d}', 'legal_name': f'ДЕМО Оператор {number}', 'brand': f'ДЕМО Бренд {number}'},
        'scope_evidence': {'geography': source('/contacts', 'Алматы'), 'industry': source('/about', 'ДЕМО стоматология'),
                           'identity': source('/legal', f'ДЕМО Оператор {number} DEMO-{number:06d}')},
        'fields': fields,
        'contact': {'current_role': True, 'email_kind': 'person_work', 'person_email_link': True,
                    'role_source_url': base + '/role', 'email_source_url': base + '/email'},
        'verification': {'reviewer': 'synthetic-fixture', 'checked_at': today, 'scope_matches': True,
                         'identity_checked': True, 'duplicates_checked': True, 'evidence_checked': True},
    }


def candidate(number, req=None, **kwargs):
    return {'kind': 'candidate', 'entity_id': f'demo-{number:03d}', 'payload': record(number, req), **kwargs}


def segment(number, state='pending', error=None):
    return {'kind': 'segment', 'entity_id': f'segment-{number}', 'payload': {
        'status': state, 'query': f'ДЕМО запрос {number}', 'cursor': 'page 1, result 1',
        'next_action': 'Открыть следующий источник', 'attempts': 0, 'processed_urls': [], 'last_error': error,
    }}

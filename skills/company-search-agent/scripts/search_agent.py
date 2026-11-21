#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
from collections import deque
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
from urllib.parse import urlparse
import zipfile
import xml.etree.ElementTree as ET

VERSION = 1
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
CODE = re.compile(r'CSA-[A-Z0-9-]{6,60}\Z')
KINDS = {'candidate', 'segment', 'coverage', 'note', 'review', 'export'}
GENERIC_EMAIL = re.compile(r'^(info|contact|contacts|office|reception|admin|support|sales|hello|mail|clinic|stom|priem|приемная)([._+-]|\d|$)', re.I)


class Invalid(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def load(path):
    with open(path, encoding='utf-8') as stream:
        return json.load(stream)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.card-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def norm(value):
    return re.sub(r'[^\w]', '', str(value).casefold().replace('ё', 'е'))


def valid_url(value, demo=False):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return False
    parsed = urlparse(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.lower()
    return demo or not (host.endswith(('.example', '.invalid', '.test', '.localhost')) or host in {'example.com', 'example.org', 'example.net', 'localhost'})


def check_request(request):
    if not isinstance(request, dict):
        raise Invalid('request must be an object')
    for key in ('original_request', 'geography', 'industry', 'evidence_policy'):
        if not isinstance(request.get(key), str) or not request[key].strip():
            raise Invalid('Missing request.' + key)
    for key in ('quota', 'batch_size', 'max_evidence_age_days'):
        if type(request.get(key)) is not int or request[key] < 1:
            raise Invalid(key + ' must be a positive integer')
    if request['quota'] > 100000 or request['batch_size'] > 1048575:
        raise Invalid('Requested size exceeds supported bounds')
    if request.get('uniqueness') not in {'company', 'brand', 'branch'}:
        raise Invalid('uniqueness must be company, brand or branch')
    fields = request.get('fields')
    if not isinstance(fields, list) or not fields or len(fields) > 500:
        raise Invalid('fields must contain 1..500 definitions')
    keys = []
    labels = []
    for field in fields:
        if not isinstance(field, dict) or not re.fullmatch(r'[a-z][a-z0-9_]{0,50}', field.get('key', '')) or not isinstance(field.get('label'), str) or not field['label'].strip():
            raise Invalid('Invalid field definition')
        keys.append(field['key'])
        labels.append(field['label'])
    if len(set(keys)) != len(keys) or len(set(labels)) != len(labels) or 'name' not in keys:
        raise Invalid('Fields need unique keys/labels and name')
    for flag in ('named_contact', 'independent_review_required'):
        if type(request.get(flag)) is not bool:
            raise Invalid(flag + ' must be boolean')
    roles = request.get('allowed_roles')
    if not isinstance(roles, list) or not all(isinstance(r, str) and r.strip() for r in roles):
        raise Invalid('allowed_roles must be a list of roles')
    if request['named_contact'] and (not roles or not {'person', 'role', 'email'}.issubset(keys)):
        raise Invalid('Named contact needs person, role, email and allowed_roles')


def source_errors(source, request):
    if not isinstance(source, dict):
        return ['source must be an object']
    errors = []
    if not valid_url(source.get('url'), request.get('demo', False)):
        errors.append('missing/invalid source URL')
    try:
        checked = date.fromisoformat(source.get('checked_at', ''))
        age = (date.today() - checked).days
        if age < 0 or age > request['max_evidence_age_days']:
            errors.append('source check date is future or stale')
    except (TypeError, ValueError):
        errors.append('invalid source check date')
    if not isinstance(source.get('quote'), str) or not source['quote'].strip():
        errors.append('missing evidence quote')
    return errors


def record_errors(record, request):
    errors = []
    if not isinstance(record, dict):
        return ['record must be an object']
    fields = record.get('fields', {})
    if not isinstance(fields, dict):
        return ['fields must be an object']
    for definition in request['fields']:
        key = definition['key']
        field = fields.get(key, {})
        if not isinstance(field, dict) or not isinstance(field.get('value'), str) or not field['value'].strip():
            errors.append(key + ': missing text value')
            continue
        sources = field.get('sources')
        if not isinstance(sources, list) or not sources:
            errors.append(key + ': no sources')
        else:
            for source in sources:
                errors.extend(key + ': ' + e for e in source_errors(source, request))
    scope = record.get('scope_evidence', {})
    if not isinstance(scope, dict):
        return errors + ['scope_evidence must be an object']
    for key in ('geography', 'industry', 'identity'):
        errors.extend(key + ': ' + e for e in source_errors(scope.get(key), request))
    identity = record.get('identity', {})
    if not isinstance(identity, dict):
        return errors + ['identity must be an object']
    if request['uniqueness'] == 'company' and not (identity.get('registration_id') or identity.get('legal_name')):
        errors.append('company needs registration_id or confirmed legal_name')
    if request['uniqueness'] == 'brand' and not identity.get('brand'):
        errors.append('brand identity missing')
    if request['uniqueness'] == 'branch' and not identity.get('branch_id'):
        errors.append('branch identity missing')
    verification = record.get('verification', {})
    if not isinstance(verification, dict):
        return errors + ['verification must be an object']
    for key in ('scope_matches', 'identity_checked', 'duplicates_checked', 'evidence_checked'):
        if verification.get(key) is not True:
            errors.append('verification.' + key + ' is not confirmed')
    if not verification.get('reviewer'):
        errors.append('verification reviewer missing')
    errors.extend('verification: ' + e for e in source_errors({
        'url': (scope.get('identity') or {}).get('url', '') if isinstance(scope.get('identity'), dict) else '',
        'checked_at': verification.get('checked_at'), 'quote': 'Review timestamp'
    }, request))
    if request['named_contact']:
        contact = record.get('contact', {})
        if not isinstance(contact, dict):
            return errors + ['contact must be an object']
        named = {key: fields.get(key) if isinstance(fields.get(key), dict) else {} for key in ('person', 'role', 'email')}
        values = {key: str(named[key].get('value') or '') for key in named}
        if norm(values['role']) not in {norm(r) for r in request['allowed_roles']}:
            errors.append('role is not allowed')
        if contact.get('current_role') is not True:
            errors.append('current role not confirmed')
        email = values['email']
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            errors.append('invalid email')
        if GENERIC_EMAIL.match(email.split('@')[0]):
            errors.append('generic email is forbidden')
        if contact.get('email_kind') != 'person_work' or contact.get('person_email_link') is not True:
            errors.append('person-work-email link not confirmed')
        for field_key, url_key, terms in (
            ('role', 'role_source_url', (values['person'], values['role'])),
            ('email', 'email_source_url', (values['person'], values['email'])),
        ):
            sources = named[field_key].get('sources') or []
            matching = [s for s in sources if isinstance(s, dict) and s.get('url') == contact.get(url_key)] if isinstance(sources, list) else []
            if not matching or not any(all(norm(term) in norm(s.get('quote', '')) for term in terms) for s in matching):
                errors.append(field_key + ': designated quote must link person and value')
    return errors


def identity_keys(record, request):
    identity = record.get('identity', {})
    keys = set()
    if request['uniqueness'] == 'branch':
        if identity.get('branch_id'):
            keys.add('branch:' + norm(identity['branch_id']))
        return keys
    if identity.get('registration_id'):
        keys.add('reg:' + norm(identity['registration_id']))
    if identity.get('legal_name'):
        keys.add('legal:' + norm(identity['legal_name']))
    if request['uniqueness'] == 'brand' and identity.get('brand'):
        keys.add('brand:' + norm(identity['brand']))
    return keys


def duplicate_pairs(records, request):
    result = []
    items = sorted(records.items())
    for index, (left_id, left) in enumerate(items):
        for right_id, right in items[index + 1:]:
            overlap = identity_keys(left, request) & identity_keys(right, request)
            if overlap:
                result.append({'left': left_id, 'right': right_id, 'reason': sorted(overlap)})
                continue
            collisions = []
            for field in ('phone', 'email'):
                lv = left.get('fields', {}).get(field, {}).get('value', '')
                rv = right.get('fields', {}).get(field, {}).get('value', '')
                if lv and rv and norm(lv) == norm(rv):
                    collisions.append(field)
            lname = left.get('fields', {}).get('name', {}).get('value', '')
            rname = right.get('fields', {}).get('name', {}).get('value', '')
            if lname and norm(lname) == norm(rname):
                collisions.append('name')
            resolutions = left.get('distinct_from', {}).get(right_id) or right.get('distinct_from', {}).get(left_id)
            if collisions and not (isinstance(resolutions, dict) and resolutions.get('reason') and valid_url(resolutions.get('url'), request.get('demo', False))):
                result.append({'left': left_id, 'right': right_id, 'reason': collisions, 'manual_review': True})
    return result


def new_card(request, code=None):
    check_request(request)
    code = code or 'CSA-' + datetime.now(timezone.utc).strftime('%Y%m%d') + '-' + uuid.uuid4().hex[:10].upper()
    if not CODE.fullmatch(code):
        raise Invalid('Invalid task code')
    card = {'schema_version': VERSION, 'code': code, 'request': copy.deepcopy(request), 'created_at': utcnow(), 'revision': 0, 'events': []}
    return refresh(card)


def heads(events):
    used = {parent for e in events for parent in e['parents']}
    return [e for e in events if e['id'] not in used]


def validate_card(card):
    if card.get('schema_version') != VERSION or not CODE.fullmatch(card.get('code', '')):
        raise Invalid('Invalid card version/code')
    check_request(card['request'])
    seen = {}
    for event in card.get('events', []):
        if not isinstance(event.get('id'), str) or not event['id'] or event['id'] in seen:
            raise Invalid('Duplicate/invalid event ID')
        if event.get('kind') not in KINDS or not isinstance(event.get('entity_id'), str) or not event['entity_id']:
            raise Invalid('Invalid event kind/entity')
        if not isinstance(event.get('payload'), dict) or not isinstance(event.get('parents'), list):
            raise Invalid('Invalid event payload/parents')
        seen[event['id']] = event
    for event in seen.values():
        for parent in event['parents']:
            if parent not in seen or (seen[parent]['kind'], seen[parent]['entity_id']) != (event['kind'], event['entity_id']):
                raise Invalid('Missing or cross-entity parent')
    pending = {eid: len(set(e['parents'])) for eid, e in seen.items()}
    children = {eid: [] for eid in seen}
    for eid, e in seen.items():
        for parent in set(e['parents']):
            children[parent].append(eid)
    queue = deque(eid for eid, count in pending.items() if count == 0)
    done = 0
    while queue:
        eid = queue.popleft()
        done += 1
        for child in children[eid]:
            pending[child] -= 1
            if pending[child] == 0:
                queue.append(child)
    if done != len(seen):
        raise Invalid('Event graph contains a cycle')


def project(card):
    validate_card(card)
    grouped = {}
    for event in card['events']:
        grouped.setdefault((event['kind'], event['entity_id']), []).append(event)
    entities, conflicts = {}, []
    for key, events in grouped.items():
        current = heads(events)
        if len(current) != 1:
            conflicts.append({'kind': key[0], 'entity_id': key[1], 'heads': sorted(e['id'] for e in current)})
        else:
            entities[key] = current[0]['payload']
    accepted, candidates, rejected, invalid = {}, {}, {}, {}
    for (kind, eid), payload in entities.items():
        if kind != 'candidate':
            continue
        state = payload.get('state')
        if state == 'accepted':
            errors = record_errors(payload, card['request'])
            if errors:
                invalid[eid] = errors
                candidates[eid] = payload
            else:
                accepted[eid] = payload
        elif state == 'rejected':
            rejected[eid] = payload
        else:
            candidates[eid] = payload
    duplicates = duplicate_pairs(accepted, card['request'])
    blocked_ids = {p[k] for p in duplicates for k in ('left', 'right')}
    count = len(set(accepted) - blocked_ids)
    current_digest = digest({'request': card['request'], 'records': accepted})
    reviews = [v for (kind, _), v in entities.items() if kind == 'review' and v.get('digest') == current_digest]
    passed = [v for v in reviews if v.get('decision') == 'pass' and v.get('reviewer')]
    independent = any(v.get('independent') is True for v in passed)
    review_ok = bool(passed) and not any(v.get('decision') == 'fail' for v in reviews)
    if card['request']['independent_review_required']:
        review_ok = review_ok and independent
    exports = [v for (kind, _), v in entities.items() if kind == 'export' and v.get('digest') == current_digest and v.get('verified') is True]
    quota_ok = count == card['request']['quota'] and len(accepted) == count
    clear = not conflicts and not invalid and not duplicates
    if quota_ok and clear and review_ok and exports:
        state = 'ready'
    elif quota_ok and clear and card['request']['independent_review_required'] and not independent:
        state = 'awaiting_independent_review'
    elif quota_ok and clear:
        state = 'awaiting_export' if review_ok else 'awaiting_review'
    else:
        state = 'open'
    segments = {eid: p for (kind, eid), p in entities.items() if kind == 'segment'}
    executable = [{'id': eid, **p} for eid, p in sorted(segments.items()) if p.get('status') in {'pending', 'running'}]
    retry = [{'id': eid, **p} for eid, p in sorted(segments.items()) if p.get('status') == 'retry']
    return {'status': state, 'accepted_count': count, 'quota': card['request']['quota'], 'remaining': max(0, card['request']['quota'] - count),
            'accepted_ids': sorted(accepted), 'candidate_ids': sorted(candidates), 'rejected_ids': sorted(rejected),
            'invalid_records': invalid, 'duplicates': duplicates, 'conflicts': conflicts, 'digest': current_digest,
            'review_ok': review_ok, 'independent_review': independent, 'next_action': (executable or retry or [{}])[0],
            'processed_sources': sorted({u for p in segments.values() for u in p.get('processed_urls', [])}),
            'segments': segments}


def refresh(card):
    card['revision'] = len(card['events'])
    card['derived'] = project(card)
    return card


def apply_operations(card, operations):
    card = copy.deepcopy(card)
    if not isinstance(operations, list):
        raise Invalid('Operations must be a list')
    for op in operations:
        kind = op.get('kind')
        if kind not in KINDS - {'export', 'review'}:
            raise Invalid('Use dedicated review/export commands for gated events')
        eid = op.get('entity_id')
        if not isinstance(eid, str) or not eid:
            raise Invalid('entity_id required')
        payload = copy.deepcopy(op.get('payload'))
        if not isinstance(payload, dict):
            raise Invalid('payload must be an object')
        if kind == 'candidate' and payload.get('state') not in {'accepted', 'rejected', 'needs_review'}:
            raise Invalid('Invalid candidate state')
        if kind == 'candidate' and payload['state'] != 'accepted' and not payload.get('reason'):
            raise Invalid('Nonaccepted candidate needs reason')
        if kind == 'segment':
            if payload.get('status') not in {'pending', 'running', 'done', 'retry', 'blocked'}:
                raise Invalid('Invalid segment status')
            if not all(isinstance(payload.get(k), str) and payload[k] for k in ('query', 'cursor', 'next_action')):
                raise Invalid('Segment needs query, cursor, next_action')
            if not isinstance(payload.get('processed_urls'), list) or type(payload.get('attempts')) is not int or payload['attempts'] < 0:
                raise Invalid('Segment needs processed_urls and nonnegative attempts')
        existing = next((e for e in card['events'] if e['id'] == op.get('id')), None)
        if existing:
            if existing['kind'] != kind or existing['entity_id'] != eid or existing['payload'] != payload or ('parents' in op and existing['parents'] != op['parents']):
                raise Invalid('Reused event ID with different content')
            continue
        append_event(card, kind, eid, payload, op.get('parents'), op.get('id'))
    return refresh(card)


def append_event(card, kind, entity_id, payload, parents=None, event_id=None):
    previous = [e for e in card['events'] if e['kind'] == kind and e['entity_id'] == entity_id]
    if parents is None:
        parents = sorted(e['id'] for e in heads(previous))
    card['events'].append({'id': event_id or uuid.uuid4().hex, 'at': utcnow(), 'kind': kind, 'entity_id': entity_id, 'parents': parents, 'payload': payload})


def merge_cards(left, right):
    validate_card(left)
    validate_card(right)
    if left['code'] != right['code'] or left['request'] != right['request']:
        raise Invalid('Cannot merge different tasks or requirements')
    result = copy.deepcopy(left)
    events = {e['id']: e for e in result['events']}
    for event in right['events']:
        if event['id'] in events and events[event['id']] != event:
            raise Invalid('Conflicting content under one event ID')
        events[event['id']] = copy.deepcopy(event)
    result['events'] = sorted(events.values(), key=lambda e: (e['at'], e['id']))
    return refresh(result)


def review(card, reviewer, decision='pass', independent=False):
    card = refresh(copy.deepcopy(card))
    state = card['derived']
    if not reviewer or decision not in {'pass', 'fail'}:
        raise Invalid('Review needs reviewer and pass/fail')
    if decision == 'pass' and (state['accepted_count'] != state['quota'] or state['invalid_records'] or state['duplicates'] or state['conflicts']):
        raise Invalid('Cannot pass review: quota/evidence/uniqueness/conflict gate failed')
    append_event(card, 'review', reviewer, {'digest': state['digest'], 'reviewer': reviewer, 'decision': decision,
                                          'independent': independent, 'at': utcnow()})
    return refresh(card)


def records_for_export(card):
    card = refresh(card)
    state = card['derived']
    if state['accepted_count'] != state['quota'] or len(state['accepted_ids']) != state['quota'] or state['invalid_records'] or state['duplicates'] or state['conflicts'] or not state['review_ok']:
        raise Invalid('Export blocked: quota, evidence, duplicates, conflicts or review not satisfied')
    records = {}
    for eid in state['accepted_ids']:
        events = [e for e in card['events'] if e['kind'] == 'candidate' and e['entity_id'] == eid]
        records[eid] = heads(events)[0]['payload']
    return records


def export_table(card):
    records = records_for_export(card)
    headers = [field['label'] for field in card['request']['fields']]
    headers += ['ID компании', 'Оператор', 'Бренд', 'Регистрационный номер']
    source_counts = {}
    for field in card['request']['fields']:
        count = max(len(r['fields'][field['key']]['sources']) for r in records.values())
        source_counts[field['key']] = count
        for index in range(count):
            suffix = f' {index + 1}' if count > 1 else ''
            headers += [field['label'] + ' — источник' + suffix, field['label'] + ' — проверено' + suffix, field['label'] + ' — цитата' + suffix]
    for name in ('География', 'Отрасль', 'Оператор'):
        headers += [name + ' — доказательство', name + ' — дата', name + ' — выдержка']
    if len(headers) > 16384:
        raise Invalid('Evidence columns exceed Excel limit')
    rows, links = [], []
    for eid, record in sorted(records.items()):
        identity = record['identity']
        row = [record['fields'][f['key']]['value'] for f in card['request']['fields']]
        row += [eid, identity.get('legal_name', ''), identity.get('brand', ''), identity.get('registration_id', '')]
        row_links = {}
        for field in card['request']['fields']:
            sources = record['fields'][field['key']]['sources']
            for index in range(source_counts[field['key']]):
                if index < len(sources):
                    source = sources[index]
                    row_links[len(row)] = source['url']
                    row += [source['url'], source['checked_at'], source['quote']]
                else:
                    row += ['', '', '']
        for key in ('geography', 'industry', 'identity'):
            source = record['scope_evidence'][key]
            row_links[len(row)] = source['url']
            row += [source['url'], source['checked_at'], source['quote']]
        rows.append(row)
        links.append(row_links)
    return headers, rows, links


def column(index):
    result = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def element(tag, attrs=None, text=None):
    node = ET.Element(tag, attrs or {})
    if text is not None:
        node.text = str(text)
    return node


def xml_bytes(root):
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def check_text(value):
    if not isinstance(value, str) or len(value.encode('utf-16-le')) // 2 > 32767:
        raise Invalid('Excel text exceeds cell limit')
    if any((ord(c) < 32 and c not in '\t\n\r') or 0xD800 <= ord(c) <= 0xDFFF or ord(c) in (0xFFFE, 0xFFFF) for c in value):
        raise Invalid('Invalid XML character in external text')


def write_xlsx(card, output):
    headers, rows, links = export_table(card)
    for row in [headers] + rows:
        for value in row:
            check_text(value)
    batch = card['request']['batch_size']
    groups = [(rows[start:start + batch], links[start:start + batch]) for start in range(0, len(rows), batch)]
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    wb = element('workbook', {'xmlns': NS, 'xmlns:r': REL})
    sheets = ET.SubElement(wb, 'sheets')
    wb_rels = element('Relationships', {'xmlns': PKG})
    types = element('Types', {'xmlns': 'http://schemas.openxmlformats.org/package/2006/content-types'})
    for ext, content in [('rels', 'application/vnd.openxmlformats-package.relationships+xml'), ('xml', 'application/xml')]:
        ET.SubElement(types, 'Default', {'Extension': ext, 'ContentType': content})
    for part, content in [('/xl/workbook.xml', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'), ('/xl/styles.xml', 'application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml')]:
        ET.SubElement(types, 'Override', {'PartName': part, 'ContentType': content})
    root_rels = element('Relationships', {'xmlns': PKG})
    ET.SubElement(root_rels, 'Relationship', {'Id': 'rId1', 'Type': REL + '/officeDocument', 'Target': 'xl/workbook.xml'})
    styles = '''<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="3"><font><sz val="10"/><name val="Arial"/><color rgb="FF172B4D"/></font><font><b/><sz val="10"/><name val="Arial"/><color rgb="FFFFFFFF"/></font><font><sz val="10"/><name val="Arial"/><color rgb="FF0563C1"/><u/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF203864"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3"><xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="49" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" horizontal="center" wrapText="1"/></xf><xf numFmtId="49" fontId="2" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('_rels/.rels', xml_bytes(root_rels))
        archive.writestr('xl/styles.xml', styles)
        ET.SubElement(wb_rels, 'Relationship', {'Id': 'styles', 'Type': REL + '/styles', 'Target': 'styles.xml'})
        for number, (group, group_links) in enumerate(groups, 1):
            name = ('ДЕМО ' if card['request'].get('demo') else 'Компании ') + f'{number:02d}'
            ET.SubElement(sheets, 'sheet', {'name': name, 'sheetId': str(number), 'r:id': f'rId{number}'})
            ET.SubElement(wb_rels, 'Relationship', {'Id': f'rId{number}', 'Type': REL + '/worksheet', 'Target': f'worksheets/sheet{number}.xml'})
            ET.SubElement(types, 'Override', {'PartName': f'/xl/worksheets/sheet{number}.xml', 'ContentType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'})
            sheet = element('worksheet', {'xmlns': NS, 'xmlns:r': REL})
            last = column(len(headers)) + str(len(group) + 1)
            ET.SubElement(sheet, 'dimension', {'ref': 'A1:' + last})
            views = ET.SubElement(sheet, 'sheetViews')
            view = ET.SubElement(views, 'sheetView', {'workbookViewId': '0', 'showGridLines': '0'})
            ET.SubElement(view, 'pane', {'ySplit': '1', 'topLeftCell': 'A2', 'state': 'frozen', 'activePane': 'bottomLeft'})
            ET.SubElement(sheet, 'sheetFormatPr', {'defaultRowHeight': '30'})
            cols = ET.SubElement(sheet, 'cols')
            for ci, header in enumerate(headers, 1):
                width = 44 if ('источник' in header or 'доказательство' in header) else 56 if ('цитата' in header or 'выдержка' in header) else 20 if ('дата' in header or 'проверено' in header) else 30
                ET.SubElement(cols, 'col', {'min': str(ci), 'max': str(ci), 'width': str(width), 'customWidth': '1'})
            data = ET.SubElement(sheet, 'sheetData')
            rels = element('Relationships', {'xmlns': PKG})
            hyperlink_defs = []
            for ri, values in enumerate([headers] + group, 1):
                row = ET.SubElement(data, 'row', {'r': str(ri), 'ht': '44' if ri == 1 else '76', 'customHeight': '1'})
                for ci, value in enumerate(values, 1):
                    ref = column(ci) + str(ri)
                    is_link = ri > 1 and ci - 1 in group_links[ri - 2]
                    cell = ET.SubElement(row, 'c', {'r': ref, 't': 'inlineStr', 's': '1' if ri == 1 else '2' if is_link else '0'})
                    string = ET.SubElement(cell, 'is')
                    ET.SubElement(string, 't', {'xml:space': 'preserve'}).text = value
                    if is_link:
                        rid = 'h' + str(len(hyperlink_defs) + 1)
                        target = group_links[ri - 2][ci - 1]
                        ET.SubElement(rels, 'Relationship', {'Id': rid, 'Type': REL + '/hyperlink', 'Target': target, 'TargetMode': 'External'})
                        hyperlink_defs.append({'ref': ref, 'r:id': rid})
            ET.SubElement(sheet, 'autoFilter', {'ref': 'A1:' + last})
            hyperlinks = ET.SubElement(sheet, 'hyperlinks')
            for attrs in hyperlink_defs:
                ET.SubElement(hyperlinks, 'hyperlink', attrs)
            archive.writestr(f'xl/worksheets/sheet{number}.xml', xml_bytes(sheet))
            archive.writestr(f'xl/worksheets/_rels/sheet{number}.xml.rels', xml_bytes(rels))
        archive.writestr('xl/workbook.xml', xml_bytes(wb))
        archive.writestr('xl/_rels/workbook.xml.rels', xml_bytes(wb_rels))
        archive.writestr('[Content_Types].xml', xml_bytes(types))
    return validate_xlsx(output, card)


def validate_xlsx(path, card):
    headers, expected_rows, expected_links = export_table(card)
    ns = {'s': NS, 'r': REL}
    batch = card['request']['batch_size']
    actual_rows = []
    sizes = []
    with zipfile.ZipFile(path) as archive:
        if archive.testzip():
            raise Invalid('Corrupt XLSX archive')
        wb = ET.fromstring(archive.read('xl/workbook.xml'))
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared = [''.join(n.itertext()) for n in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        sheets = wb.findall('s:sheets/s:sheet', ns)
        expected_count = (len(expected_rows) + batch - 1) // batch
        if len(sheets) != expected_count:
            raise Invalid('Wrong sheet count')
        for number, sheet in enumerate(sheets, 1):
            doc = ET.fromstring(archive.read(f'xl/worksheets/sheet{number}.xml'))
            if doc.find('.//s:f', ns) is not None:
                raise Invalid('External values must never become formulas')
            pane = doc.find('s:sheetViews/s:sheetView/s:pane', ns)
            if pane is None or pane.get('state') != 'frozen' or pane.get('ySplit') != '1' or doc.find('s:autoFilter', ns) is None:
                raise Invalid('Missing frozen header/filter')
            parsed = []
            for row in doc.findall('s:sheetData/s:row', ns):
                values = []
                for cell in row.findall('s:c', ns):
                    if cell.get('t') not in {'inlineStr', 's', 'str'}:
                        raise Invalid('External value is not explicitly text')
                    if cell.get('t') == 's':
                        values.append(shared[int(cell.find('s:v', ns).text)])
                    else:
                        values.append(''.join(cell.itertext()))
                parsed.append(values)
            if not parsed or parsed[0] != headers:
                raise Invalid('Wrong headers')
            start = len(actual_rows)
            expected = expected_rows[start:start + batch]
            if parsed[1:] != expected:
                raise Invalid('Exported cell values differ from accepted records')
            sizes.append(len(parsed) - 1)
            rels = ET.fromstring(archive.read(f'xl/worksheets/_rels/sheet{number}.xml.rels'))
            targets = {r.get('Id'): r.get('Target') for r in rels}
            actual = {h.get('ref'): targets.get(h.get('{' + REL + '}id')) for h in doc.findall('s:hyperlinks/s:hyperlink', ns)}
            expected_h = {column(ci + 1) + str(ri + 2): target for ri, row_links in enumerate(expected_links[start:start + batch]) for ci, target in row_links.items()}
            if actual != expected_h:
                raise Invalid('Missing or incorrect clickable evidence links')
            actual_rows.extend(parsed[1:])
    if len(actual_rows) != card['request']['quota'] or len({canonical(row) for row in actual_rows}) != len(actual_rows):
        raise Invalid('Wrong row count or duplicate rows')
    return {'verified': True, 'rows': len(actual_rows), 'sheet_sizes': sizes, 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, code):
        if not CODE.fullmatch(code):
            raise Invalid('Invalid task code')
        return self.root / (code + '.json')

    @contextmanager
    def lock(self, code):
        lock = self.path(code).with_suffix('.lock')
        try:
            lock.mkdir()
        except FileExistsError as exc:
            raise Invalid('Task is locked; retry after the other writer completes. Recover stale lock only after checking no writer is active.') from exc
        try:
            yield
        finally:
            lock.rmdir()

    def read(self, code):
        return refresh(load(self.path(code)))

    def save(self, card):
        card = refresh(card)
        atomic_json(self.path(card['code']), card)
        saved = self.read(card['code'])
        if digest(saved) != digest(card):
            raise Invalid('Readback failed')
        return saved


def main(argv=None):
    parser = argparse.ArgumentParser(description='Карточки поиска компаний и экспорт в Excel.')
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('init', 'apply', 'status', 'merge', 'review', 'export'):
        p = sub.add_parser(command)
        p.add_argument('--root', required=True)
        p.add_argument('--code', required=command != 'init')
        if command == 'init':
            p.add_argument('--request', required=True)
        elif command == 'apply':
            p.add_argument('--operations', required=True)
        elif command == 'merge':
            p.add_argument('--incoming', required=True)
        elif command == 'review':
            p.add_argument('--reviewer', required=True)
            p.add_argument('--decision', choices=['pass', 'fail'], required=True)
            p.add_argument('--independent', action='store_true')
        elif command == 'export':
            p.add_argument('--output-dir', required=True)
    args = parser.parse_args(argv)
    store = Store(args.root)
    try:
        if args.command == 'init':
            card = new_card(load(args.request), args.code)
            with store.lock(card['code']):
                if store.path(card['code']).exists():
                    raise Invalid('Task already exists')
                card = store.save(card)
        else:
            with store.lock(args.code):
                card = store.read(args.code)
                if args.command == 'apply':
                    card = store.save(apply_operations(card, load(args.operations)))
                elif args.command == 'merge':
                    card = store.save(merge_cards(card, load(args.incoming)))
                elif args.command == 'review':
                    card = store.save(review(card, args.reviewer, args.decision, args.independent))
                elif args.command == 'export':
                    output = Path(args.output_dir) / (args.code + '.xlsx')
                    output.parent.mkdir(parents=True, exist_ok=True)
                    fd, tmp = tempfile.mkstemp(dir=output.parent, suffix='.xlsx')
                    os.close(fd)
                    try:
                        result = write_xlsx(card, tmp)
                        os.replace(tmp, output)
                    finally:
                        if os.path.exists(tmp):
                            os.unlink(tmp)
                    append_event(card, 'export', 'final', {'digest': card['derived']['digest'], 'path': str(output.resolve()), **result})
                    card = store.save(card)
                    print(canonical({'file': str(output.resolve()), **result}))
        print(json.dumps({'code': card['code'], 'revision': card['revision'], **card['derived']}, ensure_ascii=False, indent=2))
        return 0
    except (Invalid, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, ET.ParseError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

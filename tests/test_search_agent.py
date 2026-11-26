import copy
from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'examples'))
from fixtures import request, record, candidate, segment
SPEC = importlib.util.spec_from_file_location('search_agent', ROOT / 'skills/company-search-agent/scripts/search_agent.py')
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


class SearchAgentTests(unittest.TestCase):
    def card(self, count=1):
        req = request(count)
        return agent.apply_operations(agent.new_card(req, 'CSA-TEST-000001'), [candidate(i, req) for i in range(1, count + 1)])

    def test_required_input_and_roles(self):
        for field in ('geography', 'industry', 'quota', 'fields', 'allowed_roles'):
            req = request()
            req[field] = None
            with self.assertRaises(agent.Invalid, msg=field):
                agent.new_card(req)

    def test_general_former_owner_and_unlinked_email_do_not_count(self):
        for mutation in ('generic', 'former', 'owner', 'unlinked', 'no_quote_link'):
            r = record()
            if mutation == 'generic':
                r['fields']['email']['value'] = 'info@clinic1.example'
            elif mutation == 'former':
                r['contact']['current_role'] = False
            elif mutation == 'owner':
                r['fields']['role']['value'] = 'владелец'
            elif mutation == 'unlinked':
                r['contact']['person_email_link'] = False
            else:
                r['fields']['email']['sources'][0]['quote'] = r['fields']['email']['value']
            card = agent.apply_operations(agent.new_card(request(1)), [{'kind': 'candidate', 'entity_id': mutation, 'payload': r}])
            self.assertEqual(card['derived']['accepted_count'], 0, mutation)
            with self.assertRaises(agent.Invalid):
                agent.records_for_export(card)

    def test_coverage_and_stale_or_missing_evidence(self):
        for change in ('future', 'stale', 'url', 'scope', 'identity'):
            r = record()
            if change in ('future', 'stale'):
                delta = 1 if change == 'future' else -31
                r['fields']['name']['sources'][0]['checked_at'] = (date.today() + timedelta(days=delta)).isoformat()
            elif change == 'url':
                r['fields']['name']['sources'][0]['url'] = 'javascript:alert(1)'
            elif change == 'scope':
                r['verification']['scope_matches'] = False
            else:
                r['identity'] = {}
            self.assertTrue(agent.record_errors(r, request()), change)

    def test_general_email_requires_exact_published_value(self):
        req = request(1, named=False)
        req['fields'].append({'key': 'email', 'label': 'Email'})
        r = record(1, req)
        email = 'a.b+sales@clinic1.example'
        r['fields']['email']['value'] = email
        r['contact'] = {'email_kind': 'general', 'current_role': False, 'person_email_link': False}
        for quote in (email, 'Email: "A.B+SALES@CLINIC1.EXAMPLE".', 'mailto:' + email,
                      "Email: '" + email + "'", 'Email: `' + email + '`'):
            with self.subTest(valid_quote=quote):
                r['fields']['email']['sources'][0]['quote'] = quote
                self.assertEqual(agent.record_errors(r, req), [])
        for quote in ('ab+sales@clinic1.example', 'a.bsales@clinic1.example',
                      email + '.other', 'other-' + email, 'Контакты указаны на сайте'):
            with self.subTest(invalid_quote=quote):
                r['fields']['email']['sources'][0]['quote'] = quote
                card = agent.apply_operations(agent.new_card(req), [{'kind': 'candidate', 'entity_id': 'clinic', 'payload': r}])
                self.assertEqual(card['derived']['accepted_count'], 0)
                self.assertIn('email: quote must contain the exact email', card['derived']['invalid_records']['clinic'])
        for value in ('not-an-email', 'info@', 'two@@clinic.example', 'info@clinic .example'):
            with self.subTest(invalid_email=value):
                r['fields']['email']['value'] = value
                self.assertIn('email: invalid email', agent.record_errors(r, req))

    def test_named_email_designated_source_preserves_punctuation(self):
        req = request(1)
        r = record(1, req)
        email = 'first.last@clinic1.example'
        r['fields']['email']['value'] = email
        person = r['fields']['person']['value']
        r['fields']['email']['sources'][0]['quote'] = person + ' — firstlast@clinic1.example'
        r['fields']['email']['sources'].append({
            'url': 'https://clinic1.example/another-publication',
            'checked_at': date.today().isoformat(), 'quote': person + ' — ' + email,
        })
        self.assertIn('email: designated quote must link person and value', agent.record_errors(r, req))
        r['fields']['email']['sources'][0]['quote'] = person + ' — ' + email
        self.assertEqual(agent.record_errors(r, req), [])

    def test_email_dedup_preserves_punctuation(self):
        req = request(2, named=False)
        req['fields'].append({'key': 'email', 'label': 'Email'})
        left, right = record(1, req), record(2, req)
        left['fields']['email']['value'] = 'a.b@clinic.example'
        for email in ('ab@clinic.example', 'a+b@clinic.example', 'a_b@clinic.example', 'a-b@clinic.example'):
            with self.subTest(email=email):
                right['fields']['email']['value'] = email
                self.assertEqual(agent.duplicate_pairs({'a': left, 'b': right}, req), [])
        right['fields']['email']['value'] = 'A.B@CLINIC.EXAMPLE'
        self.assertEqual(agent.duplicate_pairs({'a': left, 'b': right}, req)[0]['reason'], ['email'])

    def test_unresolved_evidence_conflict_blocks_acceptance_and_export(self):
        req = request(1)
        original = record(1, req)
        card = agent.review(agent.apply_operations(agent.new_card(req), [candidate(1, req)]), 'coordinator')
        disputed = copy.deepcopy(original)
        disputed['evidence_conflicts'] = ['Телефон общей поликлиники не подтверждён для стоматологического отделения']
        card = agent.apply_operations(card, [{'kind': 'candidate', 'entity_id': 'demo-001', 'payload': disputed}])
        self.assertEqual(card['derived']['accepted_count'], 0)
        self.assertEqual(card['derived']['candidate_ids'], ['demo-001'])
        with self.assertRaises(agent.Invalid):
            agent.review(card, 'coordinator')
        with self.assertRaises(agent.Invalid):
            agent.records_for_export(card)
        corrected = copy.deepcopy(disputed)
        corrected['fields']['phone']['value'] = '+7 (000) 111-1111'
        corrected['fields']['phone']['sources'][0]['quote'] = 'Стоматологическое отделение: +7 (000) 111-1111'
        corrected['reason'] = 'Подтверждён телефон стоматологического отделения'
        corrected['evidence_conflicts'] = []
        with tempfile.TemporaryDirectory() as root:
            store = agent.Store(root)
            store.save(agent.apply_operations(card, [{'kind': 'candidate', 'entity_id': 'demo-001', 'payload': corrected}]))
            resumed = agent.Store(root).read(card['code'])
            self.assertEqual(resumed['derived']['accepted_count'], 1)
            self.assertFalse(resumed['derived']['review_ok'])
            self.assertEqual(resumed['events'][-2]['payload']['evidence_conflicts'], disputed['evidence_conflicts'])
            self.assertEqual(len(agent.records_for_export(agent.review(resumed, 'coordinator'))), 1)

    def test_malformed_evidence_conflicts_fail_closed(self):
        for conflicts in (None, 'не проверено', {}, [None], ['']):
            with self.subTest(conflicts=conflicts):
                r = record()
                r['evidence_conflicts'] = conflicts
                self.assertIn('evidence_conflicts must be a list of nonempty reasons', agent.record_errors(r, request()))

    def test_real_mode_rejects_fixture_sources(self):
        req = request(1)
        req.pop('demo')
        self.assertTrue(agent.record_errors(record(), req))

    def test_dedup_registration_brand_and_contacts(self):
        for collision in ('registration_id', 'legal_name', 'brand', 'phone', 'name'):
            req = request(2)
            if collision == 'brand':
                req['uniqueness'] = 'brand'
            left, right = record(1, req), record(2, req)
            if collision in ('phone', 'name'):
                right['fields'][collision]['value'] = left['fields'][collision]['value']
            else:
                right['identity'][collision] = left['identity'][collision]
            self.assertTrue(agent.duplicate_pairs({'a': left, 'b': right}, req), collision)

    def test_branches_can_share_operator_with_documented_contacts(self):
        req = request(2)
        req['uniqueness'] = 'branch'
        left, right = record(1, req), record(2, req)
        left['identity']['branch_id'], right['identity']['branch_id'] = 'a', 'b'
        right['identity']['registration_id'] = left['identity']['registration_id']
        self.assertFalse(agent.duplicate_pairs({'a': left, 'b': right}, req))

    def test_rejection_at_quota_requires_replacement_and_new_review(self):
        card = agent.review(self.card(2), 'coordinator')
        rejected = record(1)
        rejected.update(state='rejected', reason='Общий ящик обнаружен при финальной проверке')
        card = agent.apply_operations(card, [{'kind': 'candidate', 'entity_id': 'demo-001', 'payload': rejected}])
        self.assertEqual(card['derived']['remaining'], 1)
        with self.assertRaises(agent.Invalid):
            agent.records_for_export(card)
        card = agent.apply_operations(card, [candidate(3)])
        self.assertFalse(card['derived']['review_ok'])
        card = agent.review(card, 'coordinator')
        self.assertEqual(len(agent.records_for_export(card)), 2)

    def test_independent_acceptance_is_a_separate_gate(self):
        card = self.card()
        card['request']['independent_review_required'] = True
        card = agent.review(card, 'coordinator')
        self.assertEqual(card['derived']['status'], 'awaiting_independent_review')
        with self.assertRaises(agent.Invalid):
            agent.records_for_export(card)
        card = agent.review(card, 'independent-auditor', independent=True)
        self.assertEqual(len(agent.records_for_export(card)), 1)

    def test_partial_email_search_saves_accepted_records_before_final_review(self):
        req = request(50, named=False)
        req['fields'].append({'key': 'email', 'label': 'Email'})
        req['uniqueness'] = 'branch'
        complete, incomplete = candidate(1, req), candidate(2, req)
        for op in (complete, incomplete):
            number = op['entity_id'].split('-')[-1]
            payload = op['payload']
            payload['identity']['branch_id'] = op['entity_id']
            payload['fields']['email']['value'] = f'info@clinic{number}.example'
            payload['fields']['email']['sources'][0]['quote'] = payload['fields']['email']['value']
            payload['contact'] = {'email_kind': 'general', 'current_role': False, 'person_email_link': False}
        confirmed_second = copy.deepcopy(incomplete)
        incomplete['payload']['state'] = 'needs_review'
        incomplete['payload']['reason'] = 'Не найден опубликованный email'
        incomplete['payload']['fields'].pop('email')
        card = agent.new_card(req, 'CSA-PARTIAL-EMAIL-0001')
        with tempfile.TemporaryDirectory() as root:
            store = agent.Store(root)
            store.save(agent.apply_operations(card, [complete, incomplete, segment(1)]))
            resumed = agent.Store(root).read(card['code'])
            self.assertEqual(resumed['derived']['accepted_ids'], [complete['entity_id']])
            self.assertEqual(resumed['derived']['candidate_ids'], [incomplete['entity_id']])
            self.assertEqual(resumed['derived']['remaining'], 49)
            self.assertFalse(resumed['derived']['review_ok'])
            self.assertEqual(resumed['derived']['status'], 'open')
            self.assertEqual(len([e for e in resumed['events'] if e['kind'] == 'candidate']), 2)
            with self.assertRaises(agent.Invalid):
                agent.records_for_export(resumed)
            store.save(agent.apply_operations(resumed, [confirmed_second]))
            completed = subprocess.run(
                [sys.executable, str(SPEC.origin), 'status', '--root', root, '--code', card['code']],
                capture_output=True, text=True, check=True)
            state = json.loads(completed.stdout)
            self.assertEqual(state['accepted_count'], 2)
            self.assertEqual(state['remaining'], 48)
            self.assertEqual(state['candidate_ids'], [])
            self.assertFalse(state['review_ok'])
            self.assertEqual(state['status'], 'open')

    def test_parallel_merge_is_lossless_idempotent_and_conflict_safe(self):
        base = self.card()
        left = agent.apply_operations(base, [candidate(2)])
        right = agent.apply_operations(base, [candidate(3)])
        merged = agent.merge_cards(left, right)
        self.assertEqual(len(merged['derived']['accepted_ids']), 3)
        self.assertEqual(merged, agent.merge_cards(merged, right))
        rejected = record(1)
        rejected.update(state='rejected', reason='Бывший директор')
        left = agent.apply_operations(base, [{'kind': 'candidate', 'entity_id': 'demo-001', 'payload': rejected}])
        changed = record(1)
        changed['fields']['address']['value'] = 'Новый адрес'
        right = agent.apply_operations(base, [{'kind': 'candidate', 'entity_id': 'demo-001', 'payload': changed}])
        merged = agent.merge_cards(left, right)
        self.assertEqual(merged['derived']['accepted_count'], 0)
        self.assertEqual(len(merged['derived']['conflicts']), 1)
        resolved = agent.apply_operations(merged, [{'kind': 'candidate', 'entity_id': 'demo-001', 'payload': rejected}])
        self.assertFalse(resolved['derived']['conflicts'])
        self.assertEqual(len(resolved['derived']['rejected_ids']), 1)

    def test_event_id_retry_and_tamper(self):
        card = agent.new_card(request(1))
        op = candidate(1, id='retry-stable-id')
        card = agent.apply_operations(card, [op])
        self.assertEqual(card, agent.apply_operations(card, [op]))
        op['payload']['fields']['name']['value'] = 'Changed'
        with self.assertRaises(agent.Invalid):
            agent.apply_operations(card, [op])
        other = agent.new_card(request(2), card['code'])
        with self.assertRaises(agent.Invalid):
            agent.merge_cards(card, other)

    def test_502_zero_next_branch_and_process_restart(self):
        card = agent.new_card(request(1), 'CSA-RESTART-0001')
        card = agent.apply_operations(card, [segment(1), segment(2), segment(3)])
        observed = []
        with tempfile.TemporaryDirectory() as root:
            store = agent.Store(root)
            for number, outcome in ((1, '502'), (2, 'zero'), (3, 'found')):
                self.assertEqual(card['derived']['next_action']['id'], f'segment-{number}')
                observed.append(outcome)
                op = segment(number, 'retry' if outcome == '502' else 'done', 'HTTP 502 (simulated)' if outcome == '502' else None)
                op['payload']['attempts'] = 1
                op['payload']['cursor'] = 'page 1 complete'
                ops = [op] + ([candidate(1)] if outcome == 'found' else [])
                card = store.save(agent.apply_operations(card, ops))
            completed = subprocess.run([sys.executable, str(SPEC.origin), 'status', '--root', root, '--code', card['code']], capture_output=True, text=True, check=True)
            resumed = json.loads(completed.stdout)
            self.assertEqual(resumed['accepted_count'], 1)
            self.assertEqual(resumed['next_action']['id'], 'segment-1')
            self.assertEqual(resumed['segments']['segment-2']['cursor'], 'page 1 complete')
        self.assertEqual(observed, ['502', 'zero', 'found'])

    def test_xlsx_50_five_tens_safe_text_links_and_tamper(self):
        card = self.card(50)
        r = record(1)
        r['fields']['name']['value'] = '=HYPERLINK("https://evil.example")'
        r['fields']['address']['value'] = '@SUM(A1:A9)'
        r['fields']['phone']['value'] = '+70000000001'
        r['fields']['name']['sources'].append({'url': 'https://second.example/name', 'checked_at': date.today().isoformat(), 'quote': 'Second source'})
        card = agent.apply_operations(card, [{'kind': 'candidate', 'entity_id': 'demo-001', 'payload': r}])
        card = agent.review(card, 'coordinator')
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'result.xlsx'
            result = agent.write_xlsx(card, path)
            self.assertEqual(result['sheet_sizes'], [10] * 5)
            self.assertEqual(result['rows'], 50)
            with zipfile.ZipFile(path) as archive:
                parts = {n: archive.read(n) for n in archive.namelist()}
            doc = ET.fromstring(parts['xl/worksheets/sheet1.xml'])
            ns = {'s': agent.NS}
            cell = doc.find('s:sheetData/s:row[@r="2"]/s:c', ns)
            self.assertEqual(cell.get('t'), 'inlineStr')
            self.assertIn('=HYPERLINK', ''.join(cell.itertext()))
            ET.SubElement(cell, '{' + agent.NS + '}f').text = '1+1'
            parts['xl/worksheets/sheet1.xml'] = ET.tostring(doc)
            with zipfile.ZipFile(path, 'w') as archive:
                for n, value in parts.items():
                    archive.writestr(n, value)
            with self.assertRaises(agent.Invalid):
                agent.validate_xlsx(path, card)

    def test_second_request_100_different_fields_without_code_changes(self):
        req = request(100, 17, named=False)
        req.update(geography='Минск, Беларусь', industry='Производители мебели', original_request='ДЕМО 100 производителей мебели в Минске')
        req['fields'] = [{'key': 'name', 'label': 'Производитель'}, {'key': 'website', 'label': 'Сайт'}]
        card = agent.apply_operations(agent.new_card(req), [candidate(i, req) for i in range(1, 101)])
        card = agent.review(card, 'coordinator')
        with tempfile.TemporaryDirectory() as root:
            result = agent.write_xlsx(card, Path(root) / 'second.xlsx')
        self.assertEqual(result['rows'], 100)
        self.assertEqual(result['sheet_sizes'], [17, 17, 17, 17, 17, 15])

    def test_under_and_over_quota_are_not_exportable(self):
        for quota in (1, 3):
            card = self.card(2)
            card['request']['quota'] = quota
            with self.assertRaises(agent.Invalid):
                agent.records_for_export(card)

    def test_invalid_graph_and_path(self):
        card = self.card()
        card['events'][0]['parents'] = [card['events'][0]['id']]
        with self.assertRaises(agent.Invalid):
            agent.refresh(card)
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(agent.Invalid):
                agent.Store(root).path('../../bad')

    def test_malformed_contact_record_fails_closed(self):
        for key in ('identity', 'scope_evidence', 'verification', 'contact'):
            r = record()
            r[key] = None
            self.assertTrue(agent.record_errors(r, request()), key)
        r = record()
        r['fields']['email'] = None
        self.assertTrue(agent.record_errors(r, request()))

    def test_long_lived_card_does_not_exhaust_python_recursion(self):
        card = agent.new_card(request(1))
        parent = []
        for i in range(1200):
            eid = f'note-{i}'
            card['events'].append({'id': eid, 'at': agent.utcnow(), 'kind': 'note', 'entity_id': 'log', 'parents': parent, 'payload': {'position': i}})
            parent = [eid]
        card = agent.refresh(card)
        self.assertEqual(card['revision'], 1200)

    def test_cli_ready_and_failed_export_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as root:
            store = agent.Store(root)
            card = store.save(agent.review(self.card(), 'coordinator'))
            command = [sys.executable, str(SPEC.origin), 'export', '--root', root, '--code', card['code'], '--output-dir', root]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            ready = store.read(card['code'])
            self.assertEqual(ready['derived']['status'], 'ready')
            path = Path(root) / (card['code'] + '.xlsx')
            previous_bytes = path.read_bytes()
            rejected = record()
            rejected.update(state='rejected', reason='Финальная перепроверка не подтвердила контакт')
            store.save(agent.apply_operations(ready, [{'kind': 'candidate', 'entity_id': 'demo-001', 'payload': rejected}]))
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(path.read_bytes(), previous_bytes)
            self.assertEqual(store.read(card['code'])['derived']['status'], 'open')


if __name__ == '__main__':
    unittest.main(verbosity=2)

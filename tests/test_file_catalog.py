"""Synthetic unified file views: no family health data is read by these tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills' / 'family-doctor' / 'scripts' / 'file_manager.py'


class FileCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='doctor-file-catalog-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.health = self.home / 'health-records'
        self.health.mkdir()
        self.env = {k:v for k,v in os.environ.items() if not k.startswith(('FAMILY_DOCTOR_', 'HERMES_SESSION_'))}
        self.env.update(FAMILY_DOCTOR_HOME=str(self.home), FAMILY_DOCTOR_TIMEZONE='Asia/Shanghai', PYTHONDONTWRITEBYTECODE='1')
        self.data = {'owner': {'name': '合成成员'}, 'records': {}, 'consultations': [], 'consultation_episodes': []}
        self.save()

    def write(self, name, data):
        path = self.health / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return path

    def save(self):
        self.write('user-profile.json', self.data)

    def run_cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str,args)], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        return result

    def listing(self, *args):
        return json.loads(self.run_cli('list', *args, '--json').stdout)

    def receipt(self, digest='a'*64, filename='测试报告.txt', when='2026-11-11T09:00:00+08:00'):
        key = str(uuid.uuid4())
        raw = 'raw/' + digest + '.txt'
        (self.health/'raw').mkdir(exist_ok=True)
        (self.health/raw).write_text('synthetic', encoding='utf-8')
        record = {'id':key,'status':'pending','raw_file':raw,'original_name':filename,'sha256':digest,
                  'document_type':'检验结果','imported_at':when,'reporter_name':'合成家属',
                  'pages':[{'text':'SECRET_OCR_PAGE'}], 'fields':[], 'document_date':'2020-01-02'}
        self.write('imports/'+key+'.json',record)
        return record

    def confirmed(self):
        receipt = self.receipt()
        receipt['status']='confirmed'
        self.write('imports/'+receipt['id']+'.json',receipt)
        entry=dict(receipt,confirmed_at='2026-11-11T10:00:00+08:00',upload_sources=[dict(receipt,import_id=receipt['id'])])
        entry.pop('pages')
        self.data['records']={'检验结果':[entry]}
        self.save()
        return entry

    def report(self, start='2026-11-01T09:00:00+08:00', end='2026-11-01T11:00:00+08:00'):
        key='cons_'+uuid.uuid4().hex
        record={'id':key,'patient':'合成成员','role':'self','reporter_name':'合成成员','chiefComplaint':'测试咳嗽',
                'recordedAt':'2026-11-11T10:00:00+08:00','event':{'start':start,'end':end,'symptoms':[{'key':'咳嗽'}]},
                'file':'consultations/'+key+'.md'}
        path=self.health/record['file']; path.parent.mkdir(exist_ok=True); path.write_text('synthetic')
        self.data['consultations'].append(record)
        return record

    def episode(self, *reports):
        key='episode_'+uuid.uuid4().hex
        result={'id':key,'report_ids':[r['id'] for r in reports], 'file':'consultations/episodes/'+key+'.md','status':'merged'}
        path=self.health/result['file']; path.parent.mkdir(exist_ok=True); path.write_text('synthetic')
        self.data['consultation_episodes'].append(result)
        self.save()
        return result

    def test_confirmed_duplicates_and_new_pending_upload_are_one_document(self):
        document=self.confirmed()
        duplicate=self.receipt(filename='家属重命名报告.txt')
        duplicate.update(status='confirmed',canonical_document_id=document['id'],duplicate_of=document['id'])
        self.write('imports/'+duplicate['id']+'.json',duplicate)
        self.data['records']['检验结果'][0]['upload_sources'].append(dict(duplicate,import_id=duplicate['id']))
        self.save()
        pending=self.receipt(filename='再次上传报告.txt')
        entries=self.listing('--kind','document')
        self.assertEqual(len(entries),1)
        self.assertEqual(entries[0]['upload_count'],3)
        self.assertEqual(entries[0]['pending_review_count'],1)
        self.assertEqual(len(self.listing('--status','pending')),1)
        shown=json.loads(self.run_cli('show','document:'+duplicate['id'],'--json').stdout)
        self.assertEqual(shown['id'],'document:'+document['id'])
        self.assertNotIn('SECRET_OCR_PAGE',json.dumps(entries))
        self.assertIn(pending['id'],entries[0]['source_ids'])
        self.assertEqual(len(self.listing('--query','重命名报告')),1)

    def test_pending_same_content_grouped_different_content_remains_separate(self):
        first=self.receipt(when='2026-11-11T08:00:00+08:00')
        other=self.receipt(when='2026-11-11T09:00:00+08:00')
        self.receipt(digest='b'*64)
        entries=self.listing('--kind','document')
        self.assertEqual(len(entries),2)
        shown=json.loads(self.run_cli('show','document:'+other['id'],'--json').stdout)
        self.assertEqual(shown['id'],'document:'+first['id'])
        self.assertEqual(shown['pending_review_count'],2)
        self.assertNotIn('pages',shown['details'])

    def test_default_catalog_groups_sources_and_preserves_ungrouped_legacy(self):
        first,second=self.report(),self.report()
        self.episode(first,second)
        legacy=self.report(); legacy.pop('role'); self.save()
        entries=self.listing()
        self.assertEqual(len([e for e in entries if e['kind']=='episode']),1)
        self.assertEqual(len([e for e in entries if e['kind']=='consultation']),1)
        self.assertEqual(len(self.listing('--kind','consultation')),3)
        self.assertEqual(len([e for e in self.listing('--sources') if e['kind']=='consultation']),3)
        self.assertEqual(next(e for e in entries if e['kind']=='consultation')['ownership'],'unresolved')

    def test_historical_document_uses_document_date_not_upload_date(self):
        self.confirmed()
        self.assertEqual(self.listing('--kind','document','--from','2026-11-01'),[])
        self.assertEqual(len(self.listing('--kind','document','--from','2020-01-02','--to','2020-01-02')),1)
        self.assertEqual(len(self.listing('--kind','document','--from','2026-11-01','--date-basis','recorded')),1)

    def test_timezone_and_discontinuous_episode_ranges(self):
        first=self.report('2026-11-01T16:30:00Z','2026-11-01T17:00:00Z')
        second=self.report('2026-11-10T09:00:00+08:00','2026-11-10T10:00:00+08:00')
        self.episode(first,second)
        self.assertEqual(len(self.listing('--kind','episode','--from','2026-11-02','--to','2026-11-02')),1)
        self.assertEqual(self.listing('--kind','episode','--from','2026-11-05','--to','2026-11-05'),[])

    def test_archived_visibility_canonical_override_restores_alias(self):
        document=self.confirmed(); alias=self.receipt()
        self.data['file_management']={'items':{'document:'+alias['id']:{'archived':True}},'links':[]}
        self.save()
        self.assertEqual(self.listing('--kind','document'),[])
        self.assertEqual(len(self.listing('--status','archived')),1)
        self.data['file_management']['items']['document:'+document['id']]={'archived':False}
        self.save()
        self.assertEqual(len(self.listing('--kind','document')),1)

    def test_generated_reports_have_no_inferred_clinical_date(self):
        (self.health/'reports').mkdir()
        (self.health/'reports'/'latest.md').write_text('synthetic')
        entries=self.listing('--kind','report')
        self.assertEqual(len(entries),1)
        self.assertIsNone(entries[0]['occurred_start'])
        self.assertEqual(self.listing('--kind','report','--from','2000-01-01'),[])
        self.assertEqual(len(self.listing('--kind','report','--from','2000-01-01','--date-basis','recorded')),1)

    def test_unsafe_paths_and_symlinks_fail_without_publishing(self):
        doc=self.confirmed()
        original_raw=doc['raw_file']
        self.data['records']['检验结果'][0]['raw_file']='../secret.txt'; self.save()
        self.run_cli('rebuild',ok=False)
        self.assertFalse((self.health/'catalog.md').exists())
        self.data['records']['检验结果'][0]['raw_file']=original_raw; self.save()
        raw=self.health/original_raw; raw.unlink()
        outside=self.home/'outside.txt'; outside.write_text('PRIVATE')
        raw.symlink_to(outside)
        result=self.run_cli('list',ok=False)
        self.assertNotIn('PRIVATE',result.stdout+result.stderr)

    def test_corrupt_receipt_does_not_replace_previous_catalog(self):
        self.confirmed(); self.run_cli('rebuild')
        before=(self.health/'catalog.md').read_bytes()
        (self.health/'imports'/'broken.json').write_text('{')
        self.run_cli('rebuild',ok=False)
        self.assertEqual((self.health/'catalog.md').read_bytes(),before)

    def test_rebuild_has_member_names_links_and_escaped_markdown(self):
        self.data['owner']['name']='成员[示例](bad)'; self.save()
        document=self.confirmed(); self.run_cli('rebuild')
        text=(self.health/'catalog.md').read_text()
        self.assertIn('成员\\[示例\\]\\(bad\\)',text)
        self.assertIn('[查看文件](raw/',text)
        self.assertIn('归档只影响文件列表',text)
        self.assertNotIn('SECRET_OCR_PAGE',text)
        self.assertNotIn('工具定位编号',text)
        self.assertNotIn('document:'+document['id'],text)
        self.assertEqual(self.listing('--kind','document')[0]['id'],'document:'+document['id'])

    def test_invalid_range_and_unknown_item_fail(self):
        self.run_cli('list','--from','2026-11-11','--to','2026-01-01',ok=False)
        self.run_cli('show','document:../../outside',ok=False)

    def test_pending_before_profile_initialization_remains_visible(self):
        receipt=self.receipt()
        (self.health/'user-profile.json').unlink()
        entries=self.listing('--kind','document')
        self.assertEqual(entries[0]['id'],'document:'+receipt['id'])
        self.assertEqual(entries[0]['status'],'pending')
        self.run_cli('rebuild')
        self.assertFalse((self.health/'user-profile.json').exists())

    def test_links_show_in_both_directions_and_archived_evidence_stays_linked(self):
        document=self.confirmed()
        source=self.report(); episode=self.episode(source)
        self.run_cli('link','document:'+document['id'],'episode:'+episode['id'],'--basis','合成同次就诊资料')
        documents=self.listing('--kind','document')
        events=self.listing('--kind','episode')
        self.assertEqual(documents[0]['related_episodes'][0]['id'],'episode:'+episode['id'])
        self.assertEqual(events[0]['linked_documents'][0]['id'],'document:'+document['id'])
        self.run_cli('archive','document:'+document['id'],'--reason','合成已整理事项')
        self.assertEqual(self.listing('--kind','document'),[])
        self.assertTrue(self.listing('--kind','episode')[0]['linked_documents'][0]['archived'])
        text=(self.health/'catalog.md').read_text()
        self.assertIn('另有 1 项已归档资料',text)
        self.assertIn('仍保留为医疗依据',text)

    def test_foreign_member_receipt_fails_instead_of_showing_other_name(self):
        receipt=self.receipt()
        receipt.update(subject_member_id='foreign-member', reporter_name='PRIVATE_OTHER')
        self.write('imports/'+receipt['id']+'.json',receipt)
        result=self.run_cli('list',ok=False)
        self.assertNotIn('PRIVATE_OTHER',result.stdout+result.stderr)

    def test_published_index_wins_over_stale_receipt_before_recovery(self):
        document=self.confirmed()
        receipt_path=self.health/'imports'/(document['id']+'.json')
        stale=json.loads(receipt_path.read_text())
        stale.update(status='pending', reporter_name='STALE_REPORTER', title='STALE_TITLE',
                     original_name='STALE_FILENAME.txt', document_date='2026-01-01',
                     imported_at='1990-01-01T00:00:00+08:00')
        self.write('imports/'+document['id']+'.json',stale)
        before=receipt_path.read_bytes()
        listed=self.listing('--kind','document')
        self.assertEqual(len(listed),1)
        self.assertEqual(listed[0]['source_names'],['合成家属'])
        self.assertEqual(listed[0]['pending_review_count'],0)
        self.assertEqual(listed[0]['document_date'],'2020-01-02')
        self.assertNotIn('STALE_',json.dumps(listed))
        self.assertEqual(self.listing('--status','pending'),[])
        self.run_cli('rebuild')
        self.assertNotIn('STALE_', (self.health/'catalog.md').read_text())
        self.assertEqual(receipt_path.read_bytes(),before)

    def test_published_alias_receipt_cannot_add_stale_second_reporter(self):
        document=self.confirmed()
        alias=self.receipt(filename='Second upload.txt')
        self.data['records']['检验结果'][0]['upload_sources'].append(dict(alias,import_id=alias['id'],reporter_name='另一位合成家属'))
        self.save()
        alias.update(reporter_name='STALE_ALIAS_REPORTER', canonical_document_id='wrong-id', sha256='f'*64)
        self.write('imports/'+alias['id']+'.json',alias)
        listed=self.listing('--kind','document')
        self.assertEqual(len(listed),1)
        self.assertEqual(listed[0]['source_names'],['合成家属','另一位合成家属'])
        self.assertEqual(listed[0]['pending_review_count'],0)
        self.assertEqual(listed[0]['upload_count'],2)
        self.assertNotIn('STALE_',json.dumps(listed))

    def test_episode_endpoints_and_order_use_instants_across_offsets(self):
        first=self.report('2026-11-01T08:00:00+08:00','2026-11-01T08:30:00+08:00')
        second=self.report('2026-11-01T00:10:00Z','2026-11-01T01:00:00Z')
        mixed=self.episode(first,second)
        between=self.report('2026-11-01T00:05:00Z','2026-11-01T00:06:00Z')
        later=self.episode(between)
        entries=self.listing('--kind','episode')
        self.assertEqual([entry['id'] for entry in entries],['episode:'+later['id'],'episode:'+mixed['id']])
        event=entries[1]
        self.assertEqual(event['occurred_start'],'2026-11-01T08:00:00+08:00')
        self.assertEqual(event['occurred_end'],'2026-11-01T01:00:00Z')
        self.assertEqual(event['occurred_ranges'][1]['start'],'2026-11-01T00:10:00Z')

    def test_event_endpoint_without_timezone_does_not_guess_an_instant(self):
        source=self.report('2026-11-01T08:00:00','2026-11-01T08:30:00')
        self.episode(source)
        result=self.run_cli('list','--kind','episode',ok=False)
        self.assertIn('时区',result.stderr)

    def test_duplicate_field_conflict_is_visible_in_human_list_and_show(self):
        document=self.confirmed()
        canonical={'name':'合成指标','value':1.2,'unit':'mg/L','observed_at':'2020-01-02',
                   'source_page':1,'original_text':'synthetic result 1.2 mg/L'}
        submitted=dict(canonical,value=9.9)
        document['fields']=[canonical]
        alias=self.receipt(filename='另一份报告.txt')
        document['upload_sources'].append(dict(alias,import_id=alias['id'],reporter_name='合成家属乙',
            submitted_fields=[submitted],duplicate_conflicts=[{'field':'fields','canonical':[canonical],'submitted':[submitted]}]))
        self.save()
        entries=self.listing('--kind','document')
        self.assertTrue(entries[0]['has_conflicts'])
        self.assertEqual(entries[0]['conflict_count'],1)
        listed=self.run_cli('list','--kind','document').stdout
        self.assertIn('来源差异：1 项',listed)
        shown=self.run_cli('show','document:'+document['id']).stdout
        for text in ('合成家属乙','原已核对','本次提交','合成指标','1.2 mg/L','9.9 mg/L'):
            self.assertIn(text,shown)
        self.assertEqual(json.loads((self.health/'user-profile.json').read_text())['records']['检验结果'][0]['fields'][0]['value'],1.2)

    def test_rebuild_restores_missing_event_view_before_catalog(self):
        source=self.report()
        episode=self.episode(source)
        path=self.health/episode['file']
        path.unlink()
        original=(self.health/source['file']).read_bytes()
        self.run_cli('rebuild')
        self.assertTrue(path.is_file())
        self.assertIn('合成成员',path.read_text())
        self.assertEqual((self.health/source['file']).read_bytes(),original)
        self.assertTrue(self.listing('--kind','episode')[0]['file_exists'])


if __name__ == '__main__':
    unittest.main()

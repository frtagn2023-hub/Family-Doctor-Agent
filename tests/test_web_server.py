"""Synthetic cognition import workflow and localhost HTTP boundaries."""
from contextlib import redirect_stderr
from datetime import datetime, timezone
import csv
import errno
import http.client
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import web_server as web


class WebImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cognition-web-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / 'family'
        self.home.mkdir()
        self.father = 'm_' + '1' * 32
        self.child = 'm_' + '2' * 32
        registry = {'schema_version': 1, 'name': '合成认知家庭', 'members': {
            self.father: {'id': self.father, 'name': '合成父亲'},
            self.child: {'id': self.child, 'name': '合成女儿'}},
            'profiles': {'father': self.father, 'daughter': self.child}}
        (self.home / 'family.json').write_text(json.dumps(registry), encoding='utf-8')
        # Not valid health data: the cognition server must never read it.
        private = self.home / 'members' / self.father / 'health-records'
        private.mkdir(parents=True)
        (private / 'user-profile.json').write_text('PRIVATE_HEALTH_NOT_JSON', encoding='utf-8')
        self.webdir = self.base / 'web'
        self.webdir.mkdir()
        for name, content in [('index.html','<!doctype html><title>Synthetic UI</title>'),
                              ('styles.css','body{color:green}'), ('app.js',"'use strict';")]:
            (self.webdir/name).write_text(content, encoding='utf-8')
        self.fixture = {'meta': {'mode':'demo','familyName':'合成演示家庭','generatedAt':'2026-11-11',
                                'windowDays':30,'modelEnabled':True},
            'members':[{'id':self.father,'name':'合成父亲','status':'not_assessed','label':'尚未评估',
                        'validSamples':0,'trend':[],'domains':[]}],
            'totals':{},'revision':0,'imports':[], 'files':[{'title':'DO_NOT_EXPOSE'}]}
        (self.webdir/'demo-data.json').write_text(json.dumps(self.fixture), encoding='utf-8')
        self.data = web.DashboardData(self.home, 'father', web_dir=self.webdir)
        self.server = web.DashboardServer(self.data, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.observed_at = datetime.now(timezone.utc).isoformat()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def record(self, key='source-one', **changes):
        value={'source_record_id':key,'member':'合成父亲','reporter_profile':'father',
            'observed_at':self.observed_at,'source_type':'self_expression','quality':'usable',
            'signals':['repeated_question'],'confounders':[]}
        value.update(changes)
        return value

    def content(self, *records):
        return json.dumps({'schema_version':1,'records':list(records or [self.record()])}, ensure_ascii=False)

    def chat_analysis(self):
        key = next(iter(web.DIMENSIONS))
        return {'status':'review', 'label':'MODEL_TEXT_MUST_NOT_PASS',
            'lastAnalyzedAt':self.observed_at, 'clinicalValidation':True,
            'self':{'windowCount':3,'observationDays':3,'opportunities':9,'signalDays':3},
            'caregiver':{'windowCount':1,'observationDays':1,'opportunities':2,'signalDays':1},
            'quality':{'usable':4,'limited':0,'excluded':0},
            'dimensions':[{'key':key,'label':'MODEL_TEXT_MUST_NOT_PASS','status':'possible_signal',
                'opportunities':3,'signalCount':3,'signalDays':3,'caregiverSignalDays':1,
                'reference':{'available':False,'days':0,'opportunities':0,'signalCount':0,'comparison':'unavailable'}}],
            'trend':[{'date':self.observed_at[:10],'opportunities':9,'selfSignals':3,'caregiverSignals':1}],
            'alerts':[{'id':'PRIVATE_SOURCE_IDENTIFIER','code':'repeated_self_signal','label':'MODEL_TEXT_MUST_NOT_PASS',
                'level':'review','domain':key,'sourceMode':'self','days':3,
                'firstSeen':self.observed_at[:10],'lastSeen':self.observed_at[:10]}]}

    def request(self, path='/', method='GET', headers=None, body=None):
        connection=http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        connection.request(method,path,body=body,headers=headers or {})
        response=connection.getresponse()
        result=response.status,dict(response.getheaders()),response.read()
        connection.close()
        return result

    def post(self, path, payload, **headers):
        defaults={'Content-Type':'application/json','X-Requested-With':'FamilyDoctorImport'}
        defaults.update(headers)
        return self.request(path,'POST',defaults,json.dumps(payload,ensure_ascii=False).encode())

    def preview(self, content=None):
        payload={'format':'json','content':content or self.content()}
        result=self.post('/api/import/preview',payload)
        self.assertEqual(result[0],200,result[2])
        return payload,json.loads(result[2])

    def test_snapshot_has_only_cognition_and_unassessed_is_not_low_risk(self):
        code,_,body=self.request('/api/dashboard')
        self.assertEqual(code,200,body)
        data=json.loads(body)
        self.assertEqual(data['meta']['mode'],'family')
        self.assertFalse(data['meta']['modelEnabled'])
        self.assertFalse(data['meta']['notificationsEnabled'])
        self.assertFalse(data['meta']['clinicalValidation'])
        self.assertEqual(data['meta']['reminderChannel'],'web')
        self.assertTrue(data['meta']['chatAnalysisEnabled'])
        self.assertEqual(data['meta']['analysisMode'],'hermes_structured')
        self.assertTrue(data['meta']['importEnabled'])
        self.assertNotIn('files',data)
        self.assertNotIn('events',data)
        self.assertNotIn('notices',data)
        self.assertEqual(data['members'][0]['status'],'not_assessed')
        self.assertEqual(data['members'][0]['chatAnalysis']['status'],'insufficient')
        self.assertEqual(data['members'][0]['chatAnalysis']['self']['windowCount'],0)
        self.assertTrue(all(row['status']=='not_assessable'
                            for row in data['members'][0]['chatAnalysis']['dimensions']))
        self.assertNotIn(b'PRIVATE_HEALTH',body)
        self.assertNotIn(str(self.home),body.decode())

    def test_chat_nested_projection_drops_text_identifiers_and_unknown_fields(self):
        secret='PRIVATE_CHAT_OR_SOURCE_MUST_NOT_PASS'
        analysis=self.chat_analysis()
        analysis['alerts'].append(dict(analysis['alerts'][0],code='single_signal',sourceMode='caregiver',level='info',days=2))
        analysis.update(raw_text=secret,session_id=secret,provider=secret,path=secret)
        for value in (analysis['self'],analysis['caregiver'],analysis['quality'],analysis['dimensions'][0],
                      analysis['dimensions'][0]['reference'],analysis['trend'][0],analysis['alerts'][0]):
            value.update(raw_text=secret,message_id=secret,source_record_id=secret,nested={'quote':secret})
        data=self.data.chat_store.snapshot()
        data['meta']['private']=secret
        data['members'][0].update(chatAnalysis=analysis,profile=secret)
        with mock.patch.object(self.data.chat_store,'snapshot',return_value=data):
            code,_,body=self.request('/api/dashboard')
        self.assertEqual(code,200,body)
        result=json.loads(body)
        self.assertNotIn(secret,body.decode())
        self.assertNotIn('MODEL_TEXT_MUST_NOT_PASS',body.decode())
        self.assertNotIn('PRIVATE_SOURCE_IDENTIFIER',body.decode())
        analysis=result['members'][0]['chatAnalysis']
        self.assertFalse(analysis['clinicalValidation'])
        self.assertEqual(analysis['label'],web.STATUS_LABELS['review'])
        self.assertEqual(analysis['dimensions'][0]['label'],web.DIMENSIONS[analysis['dimensions'][0]['key']])
        self.assertEqual(analysis['alerts'][0]['label'],web.ALERT_LABELS['repeated_self_signal'])
        self.assertEqual([(row['sourceMode'],row['days']) for row in analysis['alerts']], [('self',3),('caregiver',2)])
        self.assertNotIn('id',analysis['alerts'][0])
        self.assertEqual(result['members'][0]['eventCount'],0,'chat windows must not double-count imported observations')

    def test_invalid_chat_aggregate_is_unavailable_not_a_false_empty_snapshot(self):
        for invalid in ({'raw_text':'PRIVATE_BAD_FIELD'}, -1, True, 'PRIVATE_BAD_FIELD'):
            analysis=self.chat_analysis()
            analysis['self']['windowCount']=invalid
            data=self.data.chat_store.snapshot()
            data['members'][0]['chatAnalysis']=analysis
            with mock.patch.object(self.data.chat_store,'snapshot',return_value=data):
                code,_,body=self.request('/api/dashboard')
            self.assertEqual(code,503,body)
            self.assertNotIn(b'PRIVATE_BAD_FIELD',body)
            self.assertNotIn(b'members',body)
        analysis=self.chat_analysis(); analysis['alerts'][0]['sourceMode']='PRIVATE_BAD_SOURCE'
        data=self.data.chat_store.snapshot(); data['members'][0]['chatAnalysis']=analysis
        with mock.patch.object(self.data.chat_store,'snapshot',return_value=data):
            code,_,body=self.request('/api/dashboard')
        self.assertEqual(code,503)
        self.assertNotIn(b'PRIVATE_BAD_SOURCE',body)
        with mock.patch.object(self.data.chat_store,'snapshot',side_effect=ValueError('/private/CHAT_FILE')):
            code,_,body=self.request('/api/dashboard')
        self.assertEqual(code,503)
        self.assertNotIn(b'CHAT_FILE',body)

    def test_chat_and_observations_follow_updated_family_registration(self):
        self.assertEqual(len(json.loads(self.request('/api/dashboard')[2])['members']),2)
        path=self.home/'family.json'; registry=json.loads(path.read_text())
        third='m_'+'3'*32
        registry['members'][third]={'id':third,'name':'合成新成员'}
        registry['profiles']['third']=third
        registry['members'][self.father]['name']='合成父亲新姓名'
        path.write_text(json.dumps(registry))
        code,_,body=self.request('/api/dashboard')
        self.assertEqual(code,200,body)
        result=json.loads(body)
        self.assertEqual(len(result['members']),3)
        self.assertEqual(result['members'][0]['name'],'合成父亲新姓名')
        self.assertTrue(all('chatAnalysis' in row for row in result['members']))
        data=self.data.chat_store.snapshot(); data['members'].pop()
        with mock.patch.object(self.data.chat_store,'snapshot',return_value=data):
            self.assertEqual(self.request('/api/dashboard')[0],503)

    def test_chat_reminders_are_read_only_web_aggregates_without_outbound_actions(self):
        with mock.patch('subprocess.run') as run, mock.patch('urllib.request.urlopen') as send:
            code,_,body=self.request('/api/dashboard')
        self.assertEqual(code,200,body)
        run.assert_not_called(); send.assert_not_called()
        result=json.loads(body)
        self.assertFalse(result['meta']['notificationsEnabled'])
        self.assertEqual(result['meta']['reminderChannel'],'web')
        for path in ('/api/chat/analyze','/api/chat/import','/api/notifications/send'):
            self.assertEqual(self.request(path)[0],404)
            self.assertEqual(self.post(path,{'content':'PRIVATE_CHAT_TEXT'})[0],405)

    def test_corrupt_chat_file_does_not_replace_statistics_with_empty_data(self):
        directory=self.home/'cognition'; directory.mkdir()
        path=directory/'chat-observations.json'
        path.write_text('{"raw_text":"PRIVATE_CORRUPT_CHAT"',encoding='utf-8')
        before=path.read_bytes()
        code,_,body=self.request('/api/dashboard')
        self.assertEqual(code,503,body)
        self.assertNotIn(b'PRIVATE_CORRUPT_CHAT',body)
        self.assertNotIn(b'members',body)
        self.assertEqual(path.read_bytes(),before)

    def test_recorded_chat_counts_are_visible_without_source_or_original_text(self):
        text='SYNTHETIC_ORIGINAL_CHAT_MUST_STAY_PRIVATE'
        messages=[{'id':'private-user-one','role':'user','at':self.observed_at,'text':'合成上下文'},
                  {'id':'private-assistant-one','role':'assistant','at':self.observed_at,'text':'合成助手问题'},
                  {'id':'private-user-two','role':'user','at':self.observed_at,'text':text}]
        payload={'schema_version':1,'session_id':'private-session','window_id':'private-window',
                 'member':self.father,'source_mode':'self','input_mode':'text','conditions':[],'messages':messages}
        analysis=self.data.chat_store.prepare(payload)['analysisTemplate']
        analysis['dimensions']['word_finding']={'status':'possible_signal','opportunities':[
            {'response_ids':['private-user-two'],'context_ids':['private-user-one'],'outcome':'possible_signal',
             'evidence':[{'message_id':'private-user-two','quote':text}]}]}
        payload['analysis']=analysis
        self.assertTrue(self.data.chat_store.record(payload)['ok'])
        code,_,body=self.request('/api/dashboard')
        self.assertEqual(code,200,body)
        row=json.loads(body)['members'][0]
        self.assertEqual(row['eventCount'],0)
        self.assertEqual(row['chatAnalysis']['status'],'observing')
        self.assertEqual(row['chatAnalysis']['self']['opportunities'],1)
        self.assertEqual(row['chatAnalysis']['alerts'][0]['code'],'single_signal')
        self.assertEqual(row['chatAnalysis']['alerts'][0]['sourceMode'],'self')
        for secret in (text,'private-user','private-assistant','private-session','private-window','father'):
            self.assertNotIn(secret,body.decode())

    def test_preview_commit_retry_and_reimport_are_idempotent(self):
        before=set(self.home.rglob('*'))
        payload,preview=self.preview()
        self.assertTrue(preview['ok'])
        self.assertEqual(preview['new'],1)
        self.assertEqual(set(self.home.rglob('*')),before,'preview must not persist anything')
        payload['previewToken']=preview['previewToken']
        first=self.post('/api/import/commit',payload)
        self.assertEqual(first[0],200,first[2])
        second=self.post('/api/import/commit',payload)
        self.assertEqual(json.loads(second[2]),json.loads(first[2]))
        _,again=self.preview()
        self.assertEqual(again['new'],0)
        self.assertEqual(again['duplicates'],1)
        snapshot=json.loads(self.request('/api/dashboard')[2])
        self.assertEqual(snapshot['members'][0]['validSamples'],1)
        self.assertEqual(snapshot['members'][0]['status'],'needs_review')
        self.assertNotIn('source-one',json.dumps(snapshot))
        self.assertNotIn('father',json.dumps(snapshot))

    def test_bad_row_is_rejected_without_partial_import_or_value_echo(self):
        secret='PRIVATE_CONVERSATION_SHOULD_NOT_APPEAR'
        content=self.content(self.record(),self.record('source-two',raw_text=secret))
        code,_,body=self.post('/api/import/preview',{'format':'json','content':content})
        self.assertEqual(code,422,body)
        result=json.loads(body)
        self.assertFalse(result['ok'])
        self.assertTrue(result['errors'])
        self.assertNotIn('previewToken',result)
        self.assertNotIn(secret,body.decode())
        self.assertFalse((self.home/'cognition'/'observations.json').exists())

    def test_csv_import_preserves_family_source_and_groups_explicit_same_event(self):
        original=self.record(event_key='same-observation-event')
        caregiver=self.record('family-source',reporter_profile='daughter',
                              source_type='caregiver_observation',event_key='same-observation-event')
        output=io.StringIO()
        writer=csv.DictWriter(output,fieldnames=list(original))
        writer.writeheader()
        for row in (original,caregiver):
            writer.writerow({key:'|'.join(value) if isinstance(value,list) else value for key,value in row.items()})
        payload={'format':'csv','content':'\ufeff'+output.getvalue()}
        code,_,body=self.post('/api/import/preview',payload)
        self.assertEqual(code,200,body)
        payload['previewToken']=json.loads(body)['previewToken']
        code,_,body=self.post('/api/import/commit',payload)
        self.assertEqual(code,200,body)
        row=json.loads(self.request('/api/dashboard')[2])['members'][0]
        self.assertEqual(row['validSamples'],2)
        self.assertEqual(row['languageSamples'],1)
        self.assertEqual(row['eventCount'],1)

    def test_preview_token_is_bound_to_content_and_current_revision(self):
        payload,preview=self.preview()
        changed=dict(payload,content=self.content(self.record('changed')),previewToken=preview['previewToken'])
        self.assertEqual(self.post('/api/import/commit',changed)[0],409)
        other,other_preview=self.preview(self.content(self.record('other')))
        other['previewToken']=other_preview['previewToken']
        self.assertEqual(self.post('/api/import/commit',other)[0],200)
        payload['previewToken']=preview['previewToken']
        self.assertEqual(self.post('/api/import/commit',payload)[0],409)

    def test_expired_preview_and_registry_rebinding_require_new_preview(self):
        payload,preview=self.preview()
        payload['previewToken']=preview['previewToken']
        self.data._tickets[preview['previewToken']]['expires']=0
        self.assertEqual(self.post('/api/import/commit',payload)[0],409)
        payload,preview=self.preview()
        payload['previewToken']=preview['previewToken']
        path=self.home/'family.json'; registry=json.loads(path.read_text())
        registry['members'][self.father]['name']='合成父亲改名'
        path.write_text(json.dumps(registry))
        self.assertEqual(self.post('/api/import/commit',payload)[0],409)

    def test_demo_isolated_preview_works_and_commit_is_forbidden(self):
        self.server.data=web.DashboardData(home='/not-a-real-home',profile='nobody',demo=True,web_dir=self.webdir)
        code,_,body=self.request('/api/dashboard')
        self.assertEqual(code,200,body)
        result=json.loads(body)
        self.assertFalse(result['meta']['importEnabled'])
        self.assertFalse(result['meta']['modelEnabled'])
        self.assertEqual(result['meta']['demoProfileMapping'][0]['profile'],'demo-1')
        self.assertNotIn('DO_NOT_EXPOSE',body.decode())
        before=set(self.base.rglob('*'))
        content=self.content(self.record(reporter_profile='demo-1'))
        payload,preview=self.preview(content)
        self.assertFalse(preview['canCommit'])
        payload['previewToken']=preview['previewToken']
        self.assertEqual(self.post('/api/import/commit',payload)[0],403)
        self.assertEqual(set(self.base.rglob('*')),before)

    def test_demo_allows_synthetic_chat_aggregates_without_live_chat_reads(self):
        self.fixture['members'][0]['chatAnalysis']=self.chat_analysis()
        (self.webdir/'demo-data.json').write_text(json.dumps(self.fixture),encoding='utf-8')
        before=set(self.base.rglob('*'))
        with mock.patch.object(web,'ChatCognitionStore',side_effect=AssertionError('must not open live chat store')):
            self.server.data=web.DashboardData(home='/private/not-used',profile='nobody',demo=True,web_dir=self.webdir)
            code,_,body=self.request('/api/dashboard')
        self.assertEqual(code,200,body)
        result=json.loads(body)
        self.assertTrue(result['meta']['chatAnalysisEnabled'])
        self.assertEqual(result['members'][0]['chatAnalysis']['status'],'review')
        self.assertNotIn(b'MODEL_TEXT_MUST_NOT_PASS',body)
        self.assertEqual(set(self.base.rglob('*')),before)

    def test_legacy_demo_and_old_import_payloads_remain_compatible(self):
        self.server.data=web.DashboardData(demo=True,web_dir=self.webdir)
        code,_,body=self.request('/api/dashboard')
        self.assertEqual(code,200,body)
        result=json.loads(body)
        self.assertFalse(result['meta']['chatAnalysisEnabled'])
        self.assertNotIn('chatAnalysis',result['members'][0])
        self.server.data=self.data
        payload,preview=self.preview()
        payload['previewToken']=preview['previewToken']
        self.assertEqual(self.post('/api/import/commit',payload)[0],200)
        row=json.loads(self.request('/api/dashboard')[2])['members'][0]
        self.assertEqual(row['validSamples'],1)
        self.assertEqual(row['chatAnalysis']['status'],'insufficient')
        self.assertEqual(row['chatAnalysis']['self']['windowCount'],0)

    def test_templates_download_with_placeholders_and_cannot_be_imported_unedited(self):
        for format in ('json','csv'):
            code,headers,body=self.request('/api/import/template.'+format)
            self.assertEqual(code,200,body)
            self.assertIn('attachment;',headers['Content-Disposition'])
            self.assertIn('REPLACE_SOURCE_ID',body.decode('utf-8-sig'))
            code,_,result=self.post('/api/import/preview',{'format':format,'content':body.decode('utf-8-sig')})
            self.assertEqual(code,422,result)

    def test_write_request_format_size_and_csrf_boundaries(self):
        body=json.dumps({'format':'json','content':self.content()}).encode()
        self.assertEqual(self.request('/api/import/preview','POST',{'Content-Type':'application/json'},body)[0],403)
        self.assertEqual(self.post('/api/import/preview',{'format':'json','content':''},**{'Content-Type':'text/plain'})[0],415)
        self.assertEqual(self.post('/api/import/preview',{'format':'json','content':'x'*(web.MAX_CONTENT_BYTES+1)})[0],413)
        self.assertEqual(self.post('/api/import/preview',{'format':'json','content':self.content(),'home':'/private'})[0],400)
        self.assertEqual(self.post('/api/import/commit',{'format':'json','content':self.content(),'previewToken':'bogus'})[0],409)
        self.assertEqual(self.post('/api/import/preview',{'format':'json','content':self.content()},Origin='https://attacker.example')[0],403)
        headers={'Content-Type':'application/json','X-Requested-With':'FamilyDoctorImport','Transfer-Encoding':'chunked'}
        self.assertEqual(self.request('/api/import/preview','POST',headers,b'0\r\n\r\n')[0],400)
        duplicate=b'{"format":"csv","format":"json","content":""}'
        self.assertEqual(self.request('/api/import/preview','POST',{'Content-Type':'application/json','X-Requested-With':'FamilyDoctorImport'},duplicate)[0],400)

    def test_static_allowlist_symlinks_host_and_unknown_routes(self):
        self.assertEqual(self.server.server_address[0],'127.0.0.1')
        code,headers,body=self.request()
        self.assertEqual(code,200)
        self.assertIn(b'Synthetic UI',body)
        self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertIn("connect-src 'self'",headers['Content-Security-Policy'])
        for path in ('/family.json','/../family.json','/%2e%2e/family.json','/demo-data.json','/web_server.py','/api/files','/api/events'):
            self.assertEqual(self.request(path)[0],404,path)
        (self.webdir/'app.js').unlink(); (self.webdir/'app.js').symlink_to(self.home/'family.json')
        self.assertEqual(self.request('/app.js')[0],404)
        for headers in ({'Host':'attacker.example'}, {'Origin':'null'}, {'Sec-Fetch-Site':'cross-site'}):
            self.assertEqual(self.request('/api/dashboard',headers=headers)[0],403)
        self.assertEqual(self.request('/api/dashboard?home=/private')[0],400)
        self.assertEqual(self.request('/'+'x'*300)[0],414)
        for method in ('PUT','PATCH','DELETE','OPTIONS'):
            self.assertEqual(self.request('/api/import/commit',method)[0],405)

    def test_errors_and_head_do_not_expose_sensitive_details(self):
        code,headers,body=self.request('/', 'HEAD')
        self.assertEqual(code,200); self.assertEqual(body,b'')
        self.assertGreater(int(headers['Content-Length']),0)
        with mock.patch.object(self.data,'snapshot',side_effect=ValueError('/private/SECRET')):
            code,_,body=self.request('/api/dashboard')
        self.assertEqual(code,503)
        self.assertNotIn(b'SECRET',body)
        with mock.patch.object(self.data,'preview',side_effect=ValueError('/private/SECRET')):
            code,_,body=self.post('/api/import/preview',{'format':'json','content':self.content()})
        self.assertEqual(code,503); self.assertNotIn(b'SECRET',body)
        self.assertFalse(json.loads(self.request('/api/health')[2])['modelEnabled'])

    def test_startup_port_conflict_is_specific_but_other_errors_stay_private(self):
        port=self.server.server_address[1]; output=io.StringIO()
        with mock.patch.object(web,'DashboardData',return_value=self.data),redirect_stderr(output):
            self.assertEqual(web.main(['--port',str(port)]),1)
        self.assertIn('端口 {} 已被占用'.format(port),output.getvalue())
        output=io.StringIO()
        with mock.patch.object(web,'DashboardData',return_value=self.data), \
             mock.patch.object(web,'DashboardServer',side_effect=OSError(errno.EACCES,'/private/SECRET')),redirect_stderr(output):
            self.assertEqual(web.main(['--port',str(port)]),1)
        self.assertNotIn('SECRET',output.getvalue())


if __name__=='__main__':
    unittest.main()

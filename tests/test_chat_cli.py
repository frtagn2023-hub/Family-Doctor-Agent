"""Synthetic end-to-end CLI checks; never access a real household or model."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from family import initialize, add_member

class ChatCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        initialize(self.home, '合成 CLI 家庭')
        add_member(self.home, '合成成员', 'synthetic')
        start = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.payload = {'schema_version': 1, 'session_id': 'synthetic-session', 'window_id': 'synthetic-turn',
            'member': '合成成员', 'source_mode': 'self', 'input_mode': 'text', 'conditions': [],
            'messages': [
                {'id': 'synthetic-u1', 'role': 'user', 'at': start.isoformat(), 'text': 'PRIVATE_SYNTHETIC_ONLY 我准备先吃早餐再散步。'},
                {'id': 'synthetic-a1', 'role': 'assistant', 'at': (start+timedelta(seconds=1)).isoformat(), 'text': '你准备什么时候散步？'},
                {'id': 'synthetic-u2', 'role': 'user', 'at': (start+timedelta(seconds=2)).isoformat(), 'text': '吃过早餐以后去散步。'}]}
    def tearDown(self):
        self.temp.cleanup()
    def run_cli(self, command, payload=None, raw=None):
        return subprocess.run([sys.executable, str(ROOT/'cognition.py'), '--home', str(self.home), '--profile', 'synthetic', command],
            input=raw if raw is not None else json.dumps(payload or self.payload, ensure_ascii=False),
            text=True, capture_output=True, cwd=str(ROOT), timeout=15)
    def test_prepare_record_and_stats_without_retaining_raw_input(self):
        prepared=self.run_cli('chat-prepare')
        self.assertEqual(prepared.returncode,0,prepared.stderr)
        self.assertNotIn('PRIVATE_SYNTHETIC_ONLY',prepared.stdout)
        template=json.loads(prepared.stdout)['analysisTemplate']
        payload=copy.deepcopy(self.payload);payload['analysis']=template
        payload['analysis']['dimensions']['comprehension']={'status':'no_signal','opportunities':[
            {'response_ids':['synthetic-u2'],'context_ids':['synthetic-a1'],'outcome':'no_signal',
             'evidence':[{'message_id':'synthetic-u2','quote':'吃过早餐以后去散步。'}]}]}
        recorded=self.run_cli('chat-record',payload)
        self.assertEqual(recorded.returncode,0,recorded.stderr)
        repeat=self.run_cli('chat-record',payload)
        self.assertEqual(repeat.returncode,0,repeat.stderr)
        self.assertTrue(json.loads(repeat.stdout)['duplicate'])
        snapshot=self.run_cli('chat-stats')
        self.assertEqual(snapshot.returncode,0,snapshot.stderr)
        data=json.loads(snapshot.stdout)['members'][0]['chatAnalysis']
        self.assertEqual(data['self']['opportunities'],1)
        self.assertEqual(data['self']['windowCount'],1)
        for path in self.home.rglob('*'):
            if path.is_file():
                self.assertNotIn('PRIVATE_SYNTHETIC_ONLY',path.read_text())
                self.assertNotIn('吃过早餐以后去散步。',path.read_text())
    def test_invalid_json_and_duplicate_keys_never_echo_private_input(self):
        for raw in ['PRIVATE_SYNTHETIC_ONLY not json','{"schema_version":1,"schema_version":1,"PRIVATE_SYNTHETIC_ONLY":true}']:
            result=self.run_cli('chat-record',raw=raw)
            self.assertEqual(result.returncode,1)
            self.assertNotIn('PRIVATE_SYNTHETIC_ONLY',result.stdout+result.stderr)
            self.assertNotIn('Traceback',result.stderr)
        self.assertFalse((self.home/'cognition/chat-observations.json').exists())
    def test_future_message_and_unknown_analysis_field_reject_without_write(self):
        payload=copy.deepcopy(self.payload)
        payload['messages'][-1]['at']=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        result=self.run_cli('chat-prepare',payload);self.assertEqual(result.returncode,1)
        prepared=json.loads(self.run_cli('chat-prepare').stdout)
        payload=copy.deepcopy(self.payload);payload['analysis']=prepared['analysisTemplate'];payload['analysis']['diagnosis']='PRIVATE_SYNTHETIC_ONLY'
        result=self.run_cli('chat-record',payload);self.assertEqual(result.returncode,1)
        self.assertNotIn('PRIVATE_SYNTHETIC_ONLY',result.stdout+result.stderr)
        self.assertFalse((self.home/'cognition/chat-observations.json').exists())
    def test_size_limit_rejects_before_parsing_without_echo(self):
        result=self.run_cli('chat-record',raw='PRIVATE_SYNTHETIC_ONLY'+'x'*(1024*1024))
        self.assertEqual(result.returncode,1)
        self.assertNotIn('PRIVATE_SYNTHETIC_ONLY',result.stdout+result.stderr)
        self.assertFalse((self.home/'cognition/chat-observations.json').exists())

if __name__=='__main__':
    unittest.main()

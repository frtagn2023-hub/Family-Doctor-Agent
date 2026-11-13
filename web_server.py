#!/usr/bin/env python3
"""Local cognitive-observation statistics and validated imports; no clinical model."""
import copy
from datetime import date, datetime
import errno
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit
import argparse

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / 'web'
sys.path.insert(0, str(ROOT / 'skills' / 'family-doctor' / 'scripts'))
from cognition_store import CognitionStore, ImportValidationError, RevisionConflict
from chat_cognition import ChatCognitionStore, DIMENSIONS, STATUS_LABELS, ALERT_LABELS

ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/index.html': ('index.html', 'text/html; charset=utf-8'),
          '/styles.css': ('styles.css', 'text/css; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8')}
MAX_PUBLIC_FILE_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_CONTENT_BYTES = 1024 * 1024
PREVIEW_TTL_SECONDS = 15 * 60
MAX_PREVIEWS = 20
MEMBER_FIELDS = ('id', 'name', 'status', 'label', 'validSamples', 'eventCount', 'signalCount',
                 'languageSamples', 'qualityCounts', 'sourceCounts', 'lastObservedAt', 'trend', 'domains')


class PreviewConflict(ValueError):
    pass


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Non-finite JSON value')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def _count(value):
    if type(value) is not int or not 0 <= value <= 9007199254740991:
        raise ValueError('Invalid aggregate count')
    return value


def _boolean(value):
    if type(value) is not bool:
        raise ValueError('Invalid aggregate flag')
    return value


def _enum(value, choices):
    if not isinstance(value, str) or value not in choices:
        raise ValueError('Invalid aggregate category')
    return value


def _aggregate_date(value, nullable=False, day_only=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ValueError('Invalid aggregate date')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return date.fromisoformat(value).isoformat()
    if not day_only and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value):
        return datetime.fromisoformat(value.replace('Z', '+00:00')).isoformat()
    raise ValueError('Invalid aggregate date')


def _aggregate_counts(value, keys):
    if not isinstance(value, dict):
        raise ValueError('Invalid aggregate object')
    return {key: _count(value[key]) for key in keys}


def project_chat_analysis(value):
    """Project every nested value; no model text or source identifiers are public."""
    if not isinstance(value, dict):
        raise ValueError('Invalid chat aggregates')
    status = _enum(value['status'], STATUS_LABELS)
    result = {'status': status, 'label': STATUS_LABELS[status],
        'lastAnalyzedAt': _aggregate_date(value['lastAnalyzedAt'], nullable=True),
        'clinicalValidation': False}
    for role in ('self', 'caregiver'):
        result[role] = _aggregate_counts(value[role],
            ('windowCount', 'observationDays', 'opportunities', 'signalDays'))
    result['quality'] = _aggregate_counts(value['quality'], ('usable', 'limited', 'excluded'))
    for key in ('dimensions', 'trend', 'alerts'):
        if not isinstance(value[key], list):
            raise ValueError('Invalid chat aggregate list')
    result['dimensions'] = []
    seen = set()
    for row in value['dimensions']:
        if not isinstance(row, dict):
            raise ValueError('Invalid chat dimension')
        key = _enum(row['key'], DIMENSIONS)
        if key in seen:
            raise ValueError('Duplicate chat dimension')
        seen.add(key)
        reference = row['reference']
        projected = _aggregate_counts(row, ('opportunities', 'signalCount', 'signalDays', 'caregiverSignalDays'))
        projected.update(key=key, label=DIMENSIONS[key],
            status=_enum(row['status'], ('not_assessable', 'no_signal', 'possible_signal')),
            reference={**_aggregate_counts(reference, ('days', 'opportunities', 'signalCount')),
                'available': _boolean(reference['available']),
                'comparison': _enum(reference['comparison'], ('unavailable', 'more_signals', 'no_increase'))})
        result['dimensions'].append(projected)
    result['trend'] = []
    for row in value['trend']:
        projected = _aggregate_counts(row, ('opportunities', 'selfSignals', 'caregiverSignals'))
        projected['date'] = _aggregate_date(row['date'], day_only=True)
        result['trend'].append(projected)
    result['alerts'] = []
    for row in value['alerts']:
        if not isinstance(row, dict):
            raise ValueError('Invalid chat reminder')
        code = _enum(row['code'], ALERT_LABELS)
        domain = row['domain']
        if domain is not None:
            domain = _enum(domain, DIMENSIONS)
        source_mode = row.get('sourceMode')
        if source_mode is not None:
            source_mode = _enum(source_mode, ('self', 'caregiver'))
        result['alerts'].append({'code': code, 'label': ALERT_LABELS[code],
            'level': _enum(row['level'], ('info', 'review', 'urgent')), 'domain': domain, 'sourceMode': source_mode,
            'days': _count(row['days']), 'firstSeen': _aggregate_date(row['firstSeen'], nullable=True),
            'lastSeen': _aggregate_date(row['lastSeen'], nullable=True)})
    return result


def project_snapshot(payload, demo):
    """Only the statistics contract is public; old health catalogues never pass."""
    if not isinstance(payload, dict) or not isinstance(payload.get('meta'), dict):
        raise ValueError('Invalid statistics')
    if not isinstance(payload.get('members'), list) or not isinstance(payload.get('imports'), list):
        raise ValueError('Invalid statistics lists')
    if not isinstance(payload.get('totals'), dict):
        raise ValueError('Invalid statistics totals')
    result = {key: copy.deepcopy(payload[key]) for key in ('totals', 'revision') if key in payload}
    result['meta'] = {key: payload['meta'].get(key) for key in
                      ('familyName', 'generatedAt', 'windowDays', 'windowStart', 'windowEnd')}
    result['meta'].update(mode='demo' if demo else 'family', modelEnabled=False,
        notificationsEnabled=False, importEnabled=not demo, previewEnabled=True,
        chatAnalysisEnabled=any('chatAnalysis' in row for row in payload['members']),
        analysisMode='hermes_structured', clinicalValidation=False, reminderChannel='web')
    result['members'] = []
    for row in payload['members']:
        projected = {key: copy.deepcopy(row[key]) for key in MEMBER_FIELDS if key in row}
        if 'chatAnalysis' in row:
            projected['chatAnalysis'] = project_chat_analysis(row['chatAnalysis'])
        result['members'].append(projected)
    result['imports'] = [{key: row[key] for key in ('id', 'importedAt', 'new', 'duplicates') if key in row}
                         for row in payload['imports']]
    return result


class DashboardData:
    def __init__(self, home=None, profile=None, demo=False, web_dir=WEB_DIR):
        self.demo = bool(demo)
        self.web_dir = Path(web_dir)
        self.store = None if demo else CognitionStore(home, profile)
        self.chat_store = None if demo else ChatCognitionStore(self.store.home, self.store.profile)
        self._tickets = {}
        self._ticket_lock = threading.RLock()
        self._demo_mapping = []

    def asset(self, name):
        path = self.web_dir / name
        if self.web_dir.is_symlink() or path.is_symlink() or path.parent != self.web_dir:
            raise ValueError('Invalid public asset')
        if path.stat().st_size > MAX_PUBLIC_FILE_BYTES:
            raise ValueError('Public asset too large')
        return path.read_bytes()

    def _demo_payload(self):
        payload = strict_json(self.asset('demo-data.json').decode('utf-8'))
        if not isinstance(payload, dict) or payload.get('meta', {}).get('mode') != 'demo':
            raise ValueError('Demonstration data must be explicitly labelled')
        return payload

    def _import_store(self):
        if self.store is None:
            with self._ticket_lock:
                if self.store is None:
                    payload = self._demo_payload()
                    registry = {'schema_version': 1, 'name': '合成认知观察家庭', 'members': {}, 'profiles': {}}
                    mapping = []
                    for index, row in enumerate(payload['members'], 1):
                        mid = row.get('id')
                        if not isinstance(mid, str) or not re.fullmatch(r'm_[0-9a-f]{32}', mid):
                            mid = 'm_' + uuid.uuid5(uuid.NAMESPACE_URL, 'family-doctor-demo:' + str(index)).hex
                        profile = 'demo-' + str(index)
                        registry['members'][mid] = {'id': mid, 'name': row['name']}
                        registry['profiles'][profile] = mid
                        mapping.append({'name': row['name'], 'profile': profile})
                    if not registry['members']:
                        raise ValueError('Demonstration has no members')
                    self.store = CognitionStore.demo(registry, 'demo-1')
                    self._demo_mapping = mapping
        return self.store

    def snapshot(self):
        payload = self._demo_payload() if self.demo else self.store.snapshot()
        if not self.demo:
            chat = self.chat_store.snapshot(days=payload['meta']['windowDays'])
            if not isinstance(chat, dict) or not isinstance(chat.get('members'), list):
                raise ValueError('Invalid chat statistics')
            rows = chat['members']
            if any(not isinstance(row, dict) for row in rows):
                raise ValueError('Invalid chat member')
            by_id = {row['id']: row for row in rows}
            if len(by_id) != len(rows) or set(by_id) != {row['id'] for row in payload['members']}:
                raise ValueError('Family registration changed during statistics read')
            for row in payload['members']:
                summary = by_id[row['id']]
                if row['name'] != summary['name']:
                    raise ValueError('Family registration changed during statistics read')
                row['chatAnalysis'] = summary['chatAnalysis']
        result = project_snapshot(payload, self.demo)
        if self.demo:
            self._import_store()
            result['meta']['demoProfileMapping'] = copy.deepcopy(self._demo_mapping)
        return result

    def template(self, format):
        value = self._import_store().template(format)
        return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode() if format == 'json' else value.encode('utf-8-sig')

    def _prune(self):
        now = time.monotonic()
        for token in list(self._tickets):
            if self._tickets[token]['expires'] < now:
                del self._tickets[token]

    @staticmethod
    def _fingerprint(content, format):
        return hashlib.sha256((format + '\0' + content).encode()).hexdigest()

    def preview(self, content, format):
        result = self._import_store().preview(content, format=format, strict_reporter=False)
        result = copy.deepcopy(result)
        result['canCommit'] = not self.demo and result['ok']
        if result['ok']:
            with self._ticket_lock:
                self._prune()
                while len(self._tickets) >= MAX_PREVIEWS:
                    del self._tickets[next(iter(self._tickets))]
                token = secrets.token_urlsafe(32)
                self._tickets[token] = {'fingerprint': self._fingerprint(content, format),
                    'revision': result['revision'], 'digest': result['digest'],
                    'expires': time.monotonic() + PREVIEW_TTL_SECONDS, 'result': None}
            result['previewToken'] = token
        return result

    def commit(self, content, format, token):
        if self.demo:
            raise PermissionError('演示模式仅校验，不保存资料。')
        with self._ticket_lock:
            self._prune()
            ticket = self._tickets.get(token)
            if not ticket or ticket['fingerprint'] != self._fingerprint(content, format):
                raise PreviewConflict('预览已过期或文件已变化，请重新校验。')
            if ticket['result'] is not None:
                return copy.deepcopy(ticket['result'])
            result = self.store.commit(content, format=format, expected_revision=ticket['revision'],
                                       expected_digest=ticket['digest'], strict_reporter=False)
            ticket['result'] = copy.deepcopy(result)
            return result


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, data, port=8765):
        self.data = data
        super().__init__(('127.0.0.1', port), DashboardHandler)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = 'FamilyDoctor'
    sys_version = ''

    def log_message(self, format, *args):
        pass  # Neither request paths nor import data enter access logs.

    def _send(self, code, body, content_type='application/json; charset=utf-8', head=False, attachment=None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        if attachment:
            self.send_header('Content-Disposition', 'attachment; filename="' + attachment + '"')
        self.end_headers()
        if not head:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, code, payload, head=False):
        self._send(code, json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(), head=head)

    def _error(self, code, message, head=False):
        self._json(code, {'error': message}, head)

    def _allowed(self, head=False):
        port = self.server.server_address[1]
        hosts = {'127.0.0.1:' + str(port), 'localhost:' + str(port)}
        if port == 80:
            hosts.update({'127.0.0.1', 'localhost'})
        if self.headers.get_all('Host', []) not in [[host] for host in hosts]:
            self._error(403, '仅接受本机访问。', head)
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {'http://' + host for host in hosts}:
            self._error(403, '不接受跨站请求。', head)
            return False
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            self._error(403, '不接受跨站请求。', head)
            return False
        return True

    def _path(self, head=False):
        if len(self.path) > 256:
            self._error(414, '请求路径过长。', head)
            return None
        try:
            parsed = urlsplit(self.path)
        except ValueError:
            self._error(400, '请求路径无效。', head)
            return None
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            self._error(400, '此接口不接收路径参数。', head)
            return None
        return parsed.path

    def _read(self, head=False):
        if not self._allowed(head):
            return
        path = self._path(head)
        if path is None:
            return
        if path in ASSETS:
            name, mime = ASSETS[path]
            try:
                body = self.server.data.asset(name)
            except (OSError, ValueError):
                self._error(404, '页面资源不存在。', head)
                return
            self._send(200, body, mime, head)
        elif path in ('/api/dashboard', '/api/health'):
            try:
                payload = self.server.data.snapshot() if path == '/api/dashboard' else {
                    'ok': True, 'mode': 'demo' if self.server.data.demo else 'family',
                    'importEnabled': not self.server.data.demo, 'modelEnabled': False,
                    'notificationsEnabled': False, 'reminderChannel': 'web', 'clinicalValidation': False}
                self._json(200, payload, head)
            except (OSError, ValueError, TypeError, KeyError):
                self._error(503, '认知统计暂时无法读取，请检查家庭登记和观察数据。', head)
        elif path in ('/api/import/template.json', '/api/import/template.csv'):
            format = path.rsplit('.', 1)[1]
            try:
                body = self.server.data.template(format)
            except (OSError, ValueError, TypeError, KeyError):
                self._error(503, '模板暂时无法生成，请检查家庭登记。', head)
                return
            mime = 'application/json; charset=utf-8' if format == 'json' else 'text/csv; charset=utf-8'
            self._send(200, body, mime, head, 'cognition-import-template.' + format)
        else:
            self._error(404, '页面不存在。', head)

    def do_GET(self):
        self._read()

    def do_HEAD(self):
        self._read(head=True)

    def do_POST(self):
        if not self._allowed():
            return
        path = self._path()
        if path is None:
            return
        if path not in ('/api/import/preview', '/api/import/commit'):
            self._error(405, '仅支持认知观察的校验与导入。')
            return
        if self.headers.get_all('X-Requested-With') != ['FamilyDoctorImport']:
            self._error(403, '请通过本机导入页面提交。')
            return
        if self.headers.get_content_type() != 'application/json':
            self._error(415, '导入请求必须使用 JSON。')
            return
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get('Transfer-Encoding') or len(lengths) != 1:
            self._error(400, '请求长度无效。')
            return
        try:
            length = int(lengths[0])
            if str(length) != lengths[0] or length <= 0:
                raise ValueError('Invalid length')
        except ValueError:
            self._error(400, '请求长度无效。')
            return
        if length > MAX_REQUEST_BYTES:
            self._error(413, '导入文件过大，请拆分为不超过 1 MiB 的文件。')
            return
        try:
            self.connection.settimeout(15)
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError('Incomplete body')
            payload = strict_json(body.decode('utf-8'))
            required = {'format', 'content'} | ({'previewToken'} if path.endswith('/commit') else set())
            if not isinstance(payload, dict) or set(payload) != required:
                raise ValueError('Invalid request fields')
            if payload['format'] not in ('json', 'csv') or not isinstance(payload['content'], str):
                raise ValueError('Invalid content type')
            if len(payload['content'].encode('utf-8')) > MAX_CONTENT_BYTES:
                self._error(413, '导入文件过大，请拆分为不超过 1 MiB 的文件。')
                return
            if path.endswith('/commit') and (not isinstance(payload['previewToken'], str) or len(payload['previewToken']) > 128):
                raise ValueError('Invalid preview token')
        except TimeoutError:
            self._error(408, '导入请求超时，请重新选择文件。')
            return
        except (UnicodeError, ValueError, TypeError, KeyError):
            self._error(400, '导入请求格式无效，请重新选择 JSON 或 CSV 文件。')
            return
        try:
            if path.endswith('/preview'):
                result = self.server.data.preview(payload['content'], payload['format'])
                self._json(200 if result['ok'] else 422, result)
            else:
                result = self.server.data.commit(payload['content'], payload['format'], payload['previewToken'])
                self._json(200, result)
        except ImportValidationError as exc:
            self._json(422, exc.preview)
        except (RevisionConflict, PreviewConflict):
            self._error(409, '预览已过期、数据或家庭登记已变化，请重新校验后导入。')
        except PermissionError:
            self._error(403, '演示模式仅校验，不保存资料。')
        except (OSError, ValueError, TypeError, KeyError):
            self._error(503, '导入未完成，请检查家庭登记和观察数据后重试。')

    def _unsupported(self):
        if self._allowed():
            self._error(405, '此操作不受支持。')

    do_PUT = _unsupported
    do_PATCH = _unsupported
    do_DELETE = _unsupported
    do_OPTIONS = _unsupported


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--home', help='共享家庭资料目录，也可用 FAMILY_DOCTOR_FAMILY_HOME')
    parser.add_argument('--profile', help='已登记的 Hermes profile')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--demo', action='store_true', help='只使用合成示例，导入仅校验不保存')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('端口必须介于 1 和 65535')
    try:
        data = DashboardData(args.home, args.profile, args.demo)
        data.snapshot()
        for name in {value[0] for value in ASSETS.values()}:
            data.asset(name)
        server = DashboardServer(data, args.port)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, OSError) and exc.errno == errno.EADDRINUSE:
            print('无法启动认知统计页：端口 {} 已被占用，请用 --port 选择其他端口。'.format(args.port), file=sys.stderr)
        else:
            print('无法启动认知统计页，请检查家庭目录、已登记 profile、页面文件或端口。', file=sys.stderr)
        return 1
    print('认知观察统计： http://127.0.0.1:{} （{}）'.format(args.port, '合成演示，导入仅校验' if args.demo else '家庭认知统计'), flush=True)
    print('聊天分析由已启用的 Hermes 接入提供；认知提示仅在 Web 展示，不发送飞书消息。按 Ctrl+C 停止。', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Owned TLS 1.3 ticket resumption and HTTP/2 connection reuse observations."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version, PackageNotFoundError
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import time
import uuid
from urllib.parse import parse_qs, urlsplit

from fingerprint_protocols import compare
from fingerprint_transport_audit import endpoint, IDENTITY, launch, header_errors

PHASES = ('initial', 'reuse', 'goaway', 'resumed', 'reuse_after')
PROBE = """async context => {
  const phases=[];
  for (const phase of ['reuse','goaway','resumed','reuse_after']) {
    const path='/echo?context='+context+'&phase='+phase+(phase==='goaway'?'&close=1':'');
    try {
      const response=await fetch(path,{cache:'no-store'});
      if (!response.ok) throw new Error('fixture status '+response.status);
      phases.push({phase,wire:await response.json()});
    } catch (error) {
      throw new Error('context='+context+' phase='+phase+' '+String(error));
    }
  }
  return phases;
}"""


def valid_settings(rows):
    return (isinstance(rows, list) and 0 < len(rows) <= 128 and
            all(isinstance(row, list) and len(row) == 2 and type(row[0]) is int and
                0 <= row[0] <= 65535 and type(row[1]) is int and 0 <= row[1] <= 2**32 - 1 for row in rows) and
            len({row[0] for row in rows}) == len(rows))


def assess(report):
    try:
        return _assess(report)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as error:
        return ['malformed transport lifecycle evidence: ' + str(error)], []


def _assess(report):
    errors = list(report.get('errors', []))
    connections = report['connections']
    by_id = {c['id']: c for c in connections}
    if len(by_id) != len(connections) or any(type(i) is not int or i < 1 for i in by_id):
        errors.append('invalid or duplicate connection ids')
    hellos = report['client_hellos']
    runs = report['runs']
    if [r['context'] for r in runs] != [0, 1] or any(type(r['context']) is not int for r in runs):
        errors.append('two independent browser contexts were not observed')
    profiles = {'full': [], 'resumed': []}
    used = set()
    settings, orders = [], []
    for run in runs:
        phases = run['phases']
        if [p['phase'] for p in phases] != list(PHASES):
            errors.append('incomplete connection lifecycle')
            continue
        ids = [p['wire']['connection_id'] for p in phases]
        if any(type(i) is not int or i not in by_id for i in ids):
            errors.append('request references an unobserved connection')
            continue
        if not (ids[0] == ids[1] == ids[2] and ids[3] == ids[4] and ids[0] != ids[3]):
            errors.append('HTTP2 reuse / GOAWAY reconnect sequence failed')
        if used & set(ids):
            errors.append('browser contexts shared a transport connection')
        used.update(ids)
        for kind, connection_id, resumed in (('full', ids[0], False), ('resumed', ids[3], True)):
            c = by_id[connection_id]
            if c.get('alpn') != 'h2' or c.get('tls') != 'TLSv1.3' or c.get('session_reused') is not resumed:
                errors.append(kind + ': negotiated TLS13/H2 session state mismatch')
            candidates = [h for h in hellos if h.get('connection_id') == connection_id]
            if len(candidates) != 1:
                errors.append(kind + ': missing unique ClientHello/connection binding')
            else:
                h = candidates[0]
                if (41 in h['extensions']) is not resumed:
                    errors.append(kind + ': PSK offer does not match session state')
                profiles[kind].append(h)
            if not c.get('settings') or any(not valid_settings(rows) for rows in c['settings']):
                errors.append(kind + ': no HTTP2 peer SETTINGS')
            else:
                settings.append(c['settings'][0])
            streams = [r['stream'] for r in c['requests']]
            if (any(type(s) is not int or s < 1 or s % 2 != 1 for s in streams) or
                    len(set(streams)) != len(streams)):
                errors.append(kind + ': invalid or reused HTTP2 request stream')
        close = phases[2]['wire']
        for phase in phases:
            wire = phase['wire']
            headers = wire['headers']
            target = urlsplit(headers[':path'])
            query = {'context': [str(run['context'])], 'phase': [phase['phase']]}
            if phase['phase'] == 'goaway':
                query['close'] = ['1']
            if target.path != '/echo' or parse_qs(target.query) != query:
                errors.append('phase does not match observed request path')
            matches = [r for r in by_id[wire['connection_id']]['requests'] if dict(r['headers']) == headers]
            if len(matches) != 1:
                errors.append('response is not bound to one server-observed request')
                continue
            request = matches[0]
            order = [k for k, _ in request['headers'] if k.startswith(':')]
            if (request.get('pseudo_order') != order or len(order) != 4 or
                    set(order) != {':method', ':authority', ':scheme', ':path'} or
                    [k for k, _ in request['headers'][:4]] != order):
                errors.append('invalid HTTP2 pseudo-header order evidence')
            orders.append(order)
            if phase['phase'] == 'goaway' and by_id[close['connection_id']].get('goaway_stream') != request['stream']:
                errors.append('missing server GOAWAY for the closing request')
        identity = run['identity']
        wire = identity['wire']
        if (wire.get('connection_id') != ids[-1] or wire['headers'].get(':path') != '/echo' or
                len([r for r in by_id[ids[-1]]['requests'] if dict(r['headers']) == wire['headers']]) != 1):
            errors.append('identity response is not bound to the resumed connection')
        scope = {'identity': {'value': {'ua': identity['userAgent'], 'languages': identity['languages'],
                    'uaData': identity['userAgentData']}}, 'http': {'status': 'observed', 'value': identity['wire']}}
        errors.extend(header_errors(scope, require_hints=True))
    if settings and any(s != settings[0] for s in settings[1:]):
        errors.append('HTTP2 SETTINGS changed across fresh/resumed connections')
    if orders and any(o != orders[0] for o in orders[1:]):
        errors.append('HTTP2 pseudo-header order changed across fresh/resumed connections')
    comparisons = []
    for kind, values in profiles.items():
        if len(values) != 2:
            errors.append(kind + ': missing two independently observed handshakes')
        else:
            result = compare(*values)
            comparisons.append({'kind': kind, **result})
            if result['status'] != 'observed_match':
                errors.append(kind + ': TLS profile changed across browser contexts')
    return sorted(set(errors)), comparisons


NETLOG_MAX_BYTES = 8 * 1024 * 1024
NETLOG_READ_LIMIT = NETLOG_MAX_BYTES + 1024 * 1024
NETLOG_GLOBAL_LIFECYCLE_EVENTS = frozenset({
    'CERT_VERIFY_PROC_CREATED',
    'QUIC_SESSION_POOL_MARK_ALL_ACTIVE_SESSIONS_GOING_AWAY',
    'QUIC_SESSION_POOL_CLOSE_ALL_SESSIONS',
})


def diagnostic_directory(output):
    """Create a fresh child of the invocation's report directory, never a link."""
    output = Path(os.path.abspath(output))
    for path in (output, *output.parents):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('diagnostic/report path must not traverse links')
    output.parent.mkdir(parents=True, exist_ok=True)
    directory = output.parent / ('transport-lifecycle-diagnostics-' + uuid.uuid4().hex)
    directory.mkdir(mode=0o700)
    return directory


def owned_url(url, origin):
    if not isinstance(origin, str):
        return False
    base = urlsplit(origin)
    if base.scheme != 'https' or base.hostname not in ('127.0.0.1', 'localhost') or base.path or base.query or base.fragment:
        return False
    if not isinstance(url, str) or not url.startswith(origin + '/'):
        return False
    target = urlsplit(url)
    if target.fragment or target.username or target.password:
        return False
    if target.path == '/favicon.ico':
        return not target.query
    if target.path != '/echo':
        return False
    if not target.query:
        return True
    query = parse_qs(target.query, keep_blank_values=True)
    if query.get('context') not in (['0'], ['1']) or query.get('phase') not in ([p] for p in PHASES):
        return False
    expected = {'context', 'phase', 'close'} if query['phase'] == ['goaway'] else {'context', 'phase'}
    return set(query) == expected and ('close' not in query or query['close'] == ['1'])


def safe_path(path):
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('diagnostic path traverses a link')


def private_netlog_directory(directory):
    excluded = [directory.parent.resolve(), Path.cwd().resolve(), Path(__file__).resolve().parents[1]]
    excluded.extend(p.parent.resolve() for p in directory.parents if p.name.lower() == 'fingerprint-diagnostics')
    if sys.platform == 'win32':
        excluded.append(Path('C:/c/chromix'))
    for candidate in (os.environ.get('RUNNER_TEMP'), tempfile.gettempdir()):
        if not candidate:
            continue
        root = Path(candidate)
        try:
            safe_path(root)
            if not root.is_absolute() or not root.is_dir():
                continue
            root = root.resolve()
            if any(root.is_relative_to(p) for p in excluded):
                continue
            return Path(tempfile.mkdtemp(prefix='chromix-private-netlog-', dir=root))
        except (OSError, ValueError):
            continue
    raise ValueError('no private temporary root outside artifact/source/snapshot roots')


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def number(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError('nonfinite JSON number')
        return value
    def constant(_):
        raise ValueError('nonfinite JSON constant')
    return json.loads(data, object_pairs_hook=pairs, parse_float=number, parse_constant=constant)


def netlog_integer(value, minimum=0, maximum=2**31 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('invalid numeric NetLog field')
    return value


def netlog_source_id(value):
    # Chromium serializes uint32 source IDs through a signed int on the wire.
    return netlog_integer(value, minimum=-2**31)


def cdp_socket_id(value):
    if (type(value) not in (int, float) or not 0 < value <= 2**31 - 1 or int(value) != value):
        raise ValueError('invalid CDP socket ID')
    return int(value)


def netlog_time(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,20}', value) or int(value) > 2**63 - 1:
        raise ValueError('invalid NetLog tick value')
    return value


def project_netlog(value):
    """Allowlist the entire publishable envelope; never publish native bytes."""
    if not isinstance(value, dict) or not isinstance(value.get('constants'), dict):
        raise ValueError('invalid native NetLog envelope')
    original = value['constants']
    if original.get('logCaptureMode') != 'HeavilyRedacted':
        raise ValueError('native capture mode is not HeavilyRedacted')
    constants = {'logCaptureMode': 'HeavilyRedacted'}
    for key in ('logEventTypes', 'logSourceType', 'logEventPhase'):
        mapping = original.get(key)
        if not isinstance(mapping, dict) or not 0 < len(mapping) <= 4096:
            raise ValueError('invalid NetLog enum map')
        if any(not isinstance(name, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', name) for name in mapping):
            raise ValueError('unsafe NetLog enum name')
        constants[key] = {name: netlog_integer(number, maximum=65535) for name, number in mapping.items()}
        if len(set(constants[key].values())) != len(mapping):
            raise ValueError('duplicate NetLog enum value')
    events = value.get('events')
    if not isinstance(events, list) or len(events) > 100000:
        raise ValueError('invalid or oversized NetLog event list')
    projected = []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get('source'), dict):
            raise ValueError('invalid NetLog event')
        source = event['source']
        row = {'time': netlog_time(event.get('time')),
            'type': netlog_integer(event.get('type')), 'phase': netlog_integer(event.get('phase')),
            'source': {'id': netlog_source_id(source.get('id')), 'type': netlog_integer(source.get('type'))}}
        if (row['type'] not in constants['logEventTypes'].values() or
                row['phase'] not in constants['logEventPhase'].values() or
                row['source']['type'] not in constants['logSourceType'].values()):
            raise ValueError('unknown NetLog enum value')
        if 'start_time' in source:
            row['source']['start_time'] = netlog_time(source['start_time'])
        params = event.get('params', {})
        if not isinstance(params, dict):
            raise ValueError('invalid NetLog parameters')
        safe = {}
        for key in ('net_error', 'os_error', 'byte_count'):
            if key in params:
                safe[key] = netlog_integer(params[key], minimum=0 if key == 'byte_count' else -2**31)
        if 'timedout' in params:
            if type(params['timedout']) is not bool:
                raise ValueError('invalid NetLog timedout value')
            safe['timedout'] = params['timedout']
        if 'source_dependency' in params:
            dependency = params['source_dependency']
            if not isinstance(dependency, dict):
                raise ValueError('invalid NetLog dependency')
            safe['source_dependency'] = {'id': netlog_source_id(dependency.get('id')),
                'type': netlog_integer(dependency.get('type'))}
            if safe['source_dependency']['type'] not in constants['logSourceType'].values():
                raise ValueError('unknown dependency source type')
        row['params'] = safe
        projected.append(row)
    return {'constants': constants, 'events': projected}


def validate_netlog_projection(value):
    if project_netlog(value) != value:
        raise ValueError('NetLog derivative is not an exact privacy projection')


class NativeNetLog:
    def __init__(self, directory, browser_sha256):
        self.path, self.private, self.destination = None, None, None
        self.report = {'status': 'unavailable', 'gating': False, 'errors': [],
            'capture_mode': 'HeavilyRedacted', 'max_event_bytes': NETLOG_MAX_BYTES,
            'read_limit_bytes': NETLOG_READ_LIMIT,
            'browser_sha256': browser_sha256, 'platform': sys.platform, 'schema_version': 2,
            'original': {'retention': 'ephemeral_private', 'read_status': 'not_read', 'cleanup_status': 'not_created'},
            'responses': [], 'dropped_responses': 0, 'browser_closed': False,
            'qualification': 'Only a whole-envelope allowlisted derivative is published; original cleanup_status records deletion. '
                'Not acceptance evidence. Native redaction omits URLs, pool keys, close descriptions and TLS bytes. '
                'GOAWAY coverage is event-name only; native stream/error numbers are unavailable, not inferred. '
                'Global config events are temporal candidates, not proven causes. '
                'NetLog ticks and server monotonic_ns are separate clocks.'}
        self.args = []
        try:
            if directory is None:
                raise ValueError('explicit diagnostic directory required')
            directory = Path(directory)
            safe_path(directory)
            if not directory.is_absolute() or not directory.is_dir() or any(directory.iterdir()):
                raise ValueError('diagnostic directory must be absolute, existing and empty')
            if not re.fullmatch(r'transport-lifecycle-diagnostics-[0-9a-f]{32}', directory.name):
                raise ValueError('fresh diagnostic child required')
            self.private = private_netlog_directory(directory)
            self.report['original']['cleanup_status'] = 'pending'
            self.path = self.private / 'netlog.json'
            self.destination = directory / 'netlog.sanitized.json'
            self.args = ['--log-net-log=' + str(self.path), '--net-log-capture-mode=HeavilyRedacted',
                         '--net-log-max-size-mb=8']
            self.report['status'] = 'pending'
        except (OSError, ValueError) as error:
            self.report['errors'].append('capture setup failed: ' + type(error).__name__)
            self.cleanup()

    def redact(self, text):
        if self.private is not None:
            for path in (str(self.path), str(self.private)):
                for spelling in (path, path.replace('\\', '/'), path.replace('\\', '\\\\')):
                    text = re.sub(re.escape(spelling), '<ephemeral-private>', text, flags=re.IGNORECASE)
        return text

    def attach(self, context, page, index, origin):
        try:
            session = context.new_cdp_session(page)
            def response_received(event):
                response = event.get('response', {})
                url = response.get('url')
                if not owned_url(url, origin):
                    return
                if len(self.report['responses']) >= 64:
                    self.report['dropped_responses'] += 1
                    return
                try:
                    socket_id = cdp_socket_id(response.get('connectionId'))
                    if response.get('protocol') != 'h2':
                        raise ValueError('owned response is not H2')
                except ValueError:
                    if 'invalid owned CDP response metadata' not in self.report['errors']:
                        self.report['errors'].append('invalid owned CDP response metadata')
                    return
                self.report['responses'].append({'context': index, 'url': url,
                    'socket_source_id': socket_id, 'protocol': 'h2'})
            session.on('Network.responseReceived', response_received)
            session.send('Network.enable')
            if 'actual_browser_args' not in self.report:
                self.report['actual_browser_args'] = [self.redact(a) for a in
                    session.send('Browser.getBrowserCommandLine')['arguments']]
        except Exception as error:
            self.report['errors'].append('CDP context=' + str(index) + ': ' + type(error).__name__)

    def cleanup(self):
        if self.private is None:
            return
        try:
            shutil.rmtree(self.private)
            self.report['original']['cleanup_status'] = 'deleted'
        except FileNotFoundError:
            self.report['original']['cleanup_status'] = 'deleted'
        except OSError as error:
            self.report['original']['cleanup_status'] = 'error'
            self.report['errors'].append('private original cleanup failed: ' + type(error).__name__)
            if self.report['status'] == 'captured':
                self.report['status'] = 'partial'

    def finish(self, report):
        if self.report['original']['cleanup_status'] == 'deleted':
            return
        if self.path is None:
            self.cleanup()
            return
        original = self.report['original']
        stage = 'native_read'
        try:
            safe_path(self.path)
            info = self.path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('unsafe native file')
            original['size_bytes'] = info.st_size
            if info.st_size > NETLOG_READ_LIMIT:
                original['read_status'] = 'oversized'
                raise ValueError('oversized native file')
            with self.path.open('rb') as stream:
                data = stream.read(NETLOG_READ_LIMIT + 1)
            if len(data) > NETLOG_READ_LIMIT:
                original['read_status'] = 'oversized'
                raise ValueError('oversized native file')
            after = self.path.stat()
            if (len(data) != info.st_size or (after.st_size, after.st_mtime_ns, after.st_ino) !=
                    (info.st_size, info.st_mtime_ns, info.st_ino)):
                raise ValueError('native file changed during read')
            original.update(read_status='hashed', sha256=hashlib.sha256(data).hexdigest())
            if info.st_size >= NETLOG_MAX_BYTES:
                self.report['errors'].append('native event size cap may have truncated capture')
            stage = 'browser_close'
            if not self.report['browser_closed']:
                raise ValueError('browser close incomplete; flush unverified')
            stage = 'json_parse'
            native = strict_json(data)
            stage = 'privacy_projection'
            value = project_netlog(native)
            validate_netlog_projection(value)
            derivative = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'
            if len(derivative) > NETLOG_READ_LIMIT:
                raise ValueError('oversized derivative')
            stage = 'derivative_write'
            safe_path(self.destination)
            with self.destination.open('xb') as stream:
                stream.write(derivative)
            self.report['derivative'] = {'path': self.destination.parent.name + '/' + self.destination.name,
                'sha256': hashlib.sha256(derivative).hexdigest(), 'size_bytes': len(derivative),
                'format': 'chromium-netlog-privacy-projection-v1', 'privacy_projection': 'whole-envelope-allowlist-v1'}
            stage = 'binding_summary'
            self.report.update(summarize_netlog(value, self.report['responses'], report))
            self.report['errors'].extend(self.report['binding_errors'])
            if self.report['summary_limit_reached'] or self.report['dropped_responses']:
                self.report['errors'].append('diagnostic summary truncated')
            self.report['status'] = 'captured' if not self.report['errors'] else 'partial'
        except Exception as error:
            self.report['status'] = 'error'
            if original['read_status'] == 'not_read':
                original['read_status'] = 'unavailable'
            # Native parser and filesystem exceptions may contain private data/paths.
            self.report['errors'].append(stage + ' failed: ' + type(error).__name__)
        finally:
            self.cleanup()


def response_server_requests(report, context, path):
    runs = [r for r in report.get('runs', []) if type(r.get('context')) is int and r['context'] == context]
    if len(runs) != 1:
        return []
    run = runs[0]
    wires = [p['wire'] for p in run.get('phases', [])
             if p.get('phase') in PHASES and path ==
             f'/echo?context={context}&phase={p["phase"]}' + ('&close=1' if p['phase'] == 'goaway' else '')]
    if path == '/echo':
        wires = [run.get('identity', {}).get('wire', {})]
    connections = report.get('connections', [])
    if path == '/favicon.ico':
        owned_wires = [p.get('wire', {}) for p in run.get('phases', [])]
        owned_wires.append(run.get('identity', {}).get('wire', {}))
        ids = {w['connection_id'] for w in owned_wires
               if type(w.get('connection_id')) is int and w['connection_id'] > 0}
        return [{'connection_id': c['id'], 'stream': r['stream']}
            for c in connections if type(c.get('id')) is int and c['id'] in ids
            for r in c.get('requests', []) if dict(r['headers']).get(':path') == path]
    if len(wires) != 1:
        return []
    wire = wires[0]
    if (type(wire.get('connection_id')) is not int or wire['connection_id'] <= 0 or
            not isinstance(wire.get('headers'), dict) or wire['headers'].get(':path') != path):
        return []
    candidates = [c for c in connections if type(c.get('id')) is int and c['id'] == wire['connection_id']]
    if len(candidates) != 1:
        return []
    return [{'connection_id': wire['connection_id'], 'stream': r['stream']}
        for r in candidates[0].get('requests', []) if dict(r['headers']) == wire['headers']]


def summarize_netlog(value, responses, report):
    validate_netlog_projection(value)
    constants, events = value['constants'], value['events']
    names = {v: k for k, v in constants['logEventTypes'].items()}
    sources = {v: k for k, v in constants['logSourceType'].items()}
    phases = {v: k for k, v in constants['logEventPhase'].items()}
    normalized = [(e['source']['type'], e['source']['id'], names[e['type']], e) for e in events]
    edges = [(kind, sid, e['params'].get('source_dependency')) for kind, sid, name, e in normalized
             if name == 'HTTP2_SESSION_INITIALIZED']
    bindings, selected, errors = [], set(), []
    origin = report.get('diagnostics', {}).get('origin', '')
    for response in responses:
        url, context, socket_id = response.get('url'), response.get('context'), response.get('socket_source_id')
        if not owned_url(url, origin):
            errors.append('non-owned response binding rejected')
            continue
        try:
            socket_id = cdp_socket_id(socket_id)
            if type(context) is not int or context not in (0, 1):
                raise ValueError('invalid context')
        except ValueError:
            errors.append('invalid context/socket response binding')
            continue
        issues = []
        seeds = {(kind, sid) for kind, sid, _, _ in normalized if sources[kind] == 'SOCKET' and sid == socket_id}
        direct = [(kind, sid) for kind, sid, dep in edges if dep is not None and
                  (dep['type'], dep['id']) in seeds and sources[kind] == 'HTTP2_SESSION']
        session = direct[0] if len(direct) == 1 else None
        if not seeds or session is None:
            issues.append('missing unique direct H2 session/socket initialization')
        elif sum((kind, sid) == session for kind, sid, _ in edges) != 1:
            issues.append('H2 session has contradictory initialization edges')
        if response.get('protocol') != 'h2':
            issues.append('owned response is not H2')
        visited = seeds | ({session} if session else set())
        selected.update(visited)
        target = urlsplit(url)
        path = target.path + ('?' + target.query if target.query else '')
        matches = response_server_requests(report, context, path)
        if len(matches) != 1:
            issues.append('missing unique server request')
        bindings.append({'context': context, 'url': url, 'socket_source_id': socket_id,
            'session_source_id': session[1] if session else None, 'server_requests': matches,
            'sources': [{'type': sources[kind], 'id': sid} for kind, sid in sorted(visited)], 'issues': issues})
    relationships = {}
    for b in bindings:
        if len(b['server_requests']) != 1 or b['session_source_id'] is None:
            continue
        triple = (b['server_requests'][0]['connection_id'], b['socket_source_id'], b['session_source_id'])
        for index, identity in enumerate(triple):
            relations, contexts = relationships.setdefault((index, identity), (set(), set()))
            relations.add(triple)
            contexts.add(b['context'])
    coverage = {}
    for b in bindings:
        if len(b['server_requests']) != 1 or b['session_source_id'] is None:
            continue
        triple = (b['server_requests'][0]['connection_id'], b['socket_source_id'], b['session_source_id'])
        if any(len(relationships[(i, identity)][0]) != 1 for i, identity in enumerate(triple)):
            b['issues'].append('non-bijective server/socket/session mapping')
        if any(len(relationships[(i, identity)][1]) != 1 for i, identity in enumerate(triple)):
            b['issues'].append('native/server connection shared across contexts')
        if triple not in coverage:
            server_id, socket_id, session_id = triple
            socket_ends = [e for kind, sid, name, e in normalized if sources[kind] == 'SOCKET' and
                           sid == socket_id and name == 'SOCKET_ALIVE' and phases[e['phase']] == 'PHASE_END']
            closes = [e for kind, sid, name, e in normalized if sources[kind] == 'HTTP2_SESSION' and
                      sid == session_id and name == 'HTTP2_SESSION_CLOSE']
            goaways = [e for kind, sid, name, e in normalized if sources[kind] == 'HTTP2_SESSION' and
                       sid == session_id and name == 'HTTP2_SESSION_RECV_GOAWAY']
            initializations = [e for kind, sid, name, e in normalized if sources[kind] == 'HTTP2_SESSION' and
                               sid == session_id and name == 'HTTP2_SESSION_INITIALIZED']
            expected_goaway = any(c['id'] == server_id and 'goaway_stream' in c for c in report.get('connections', []))
            missing = []
            if len(socket_ends) != 1:
                missing.append('missing unique SOCKET_ALIVE END')
            if len(closes) != 1 or 'net_error' not in closes[0]['params']:
                missing.append('missing unique HTTP2_SESSION_CLOSE with net_error')
            if expected_goaway and len(goaways) != 1:
                missing.append('missing unique HTTP2_SESSION_RECV_GOAWAY')
            if not expected_goaway and goaways:
                missing.append('native GOAWAY lacks server GOAWAY binding')
            if len(initializations) == len(closes) == len(socket_ends) == 1:
                start, close, end = (int(e['time']) for e in (initializations[0], closes[0], socket_ends[0]))
                if not start <= close <= end or any(not start <= int(e['time']) <= close for e in goaways):
                    missing.append('contradictory native terminal/GOAWAY ordering')
            coverage[triple] = {'connection_id': server_id, 'socket_source_id': socket_id,
                'session_source_id': session_id, 'socket_end_count': len(socket_ends),
                'session_close_count': len(closes), 'goaway_expected': expected_goaway,
                'recv_goaway_count': len(goaways), 'goaway_numeric_fields': 'not_retained_by_native_redaction',
                'issues': missing}
        b['issues'].extend(coverage[triple]['issues'])
    expected = {(context, f'/echo?context={context}&phase={phase}' + ('&close=1' if phase == 'goaway' else ''))
                for context in (0, 1) for phase in PHASES}
    if ([r.get('context') for r in report.get('runs', [])] != [0, 1] or
            any(type(r.get('context')) is not int or
                [p.get('phase') for p in r.get('phases', [])] != list(PHASES) for r in report.get('runs', []))):
        errors.append('incomplete phase collection for native bindings')
    for context, path in expected:
        candidates = [b for b in bindings if b['context'] == context and b['url'] == origin + path]
        if len(candidates) != 1:
            errors.append('missing unique phase URL binding')
            for b in candidates:
                b['issues'].append('duplicate phase URL binding')
    for b in bindings:
        if sum(other['context'] == b['context'] and other['url'] == b['url'] for other in bindings) != 1:
            b['issues'].append('duplicate response URL binding')
        b['binding_status'] = 'unresolved' if b['issues'] else 'linked'
        errors.extend(b['issues'])
    relevant = []
    for index, (kind, sid, name, event) in enumerate(normalized):
        global_config = (name in NETLOG_GLOBAL_LIFECYCLE_EVENTS or
            any(word in name for word in ('CONFIG_CHANGED', 'CERTIFICATE_DATABASE', 'NETWORK_CHANGED',
                                         'IP_ADDRESSES_CHANGED', 'NETWORK_CONNECTIVITY_CHANGED')))
        if not global_config and ((kind, sid) not in selected or not name.startswith(('HTTP2_', 'SOCKET_', 'SSL_', 'TCP_'))):
            continue
        if len(relevant) < 4096:
            relevant.append({'derivative_index': index, 'type': name, 'time': event['time'],
                'phase': phases[event['phase']], 'source': {'type': sources[kind], 'id': sid},
                'scope': 'global_candidate' if global_config else 'owned_socket_dependency', 'params': event['params']})
    return {'event_count': len(events), 'events': relevant, 'bindings': bindings,
        'coverage': list(coverage.values()), 'binding_errors': sorted(set(errors)),
        'summary_limit_reached': len(relevant) == 4096,
        'close_analysis': netlog_close_analysis(relevant, bindings, list(coverage.values()), len(relevant) == 4096),
        'unresolved_bindings': sum(b['binding_status'] != 'linked' for b in bindings)}


def netlog_close_analysis(events, bindings, coverage, truncated):
    """Describe native close observations without assigning a caller or cause."""
    candidates = [e for e in events if e['scope'] == 'global_candidate']
    rows = []
    for connection in coverage:
        socket_id, session_id = connection['socket_source_id'], connection['session_source_id']
        bound = [b for b in bindings if b['socket_source_id'] == socket_id and b['session_source_id'] == session_id]
        session = [e for e in events if e['scope'] == 'owned_socket_dependency' and
                   e['source'] == {'type': 'HTTP2_SESSION', 'id': session_id}]
        starts = [e for e in session if e['type'] == 'HTTP2_SESSION_INITIALIZED']
        closes = [e for e in session if e['type'] == 'HTTP2_SESSION_CLOSE']
        goaways = [e for e in session if e['type'] == 'HTTP2_SESSION_RECV_GOAWAY']
        ends = [e for e in events if e['source'] == {'type': 'SOCKET', 'id': socket_id} and
                e['type'] == 'SOCKET_ALIVE' and e['phase'] == 'PHASE_END']
        complete = (not truncated and not connection['issues'] and bool(bound) and
                    all(b['binding_status'] == 'linked' for b in bound) and
                    len(starts) == len(closes) == len(ends) == 1)
        during = []
        observation = 'incomplete_or_ambiguous'
        if complete:
            start, close = int(starts[0]['time']), int(closes[0]['time'])
            during = [e['derivative_index'] for e in candidates if start <= int(e['time']) <= close]
            if goaways:
                observation = 'peer_goaway_observed'
            elif closes[0]['params'].get('net_error') == 0:
                observation = 'ok_close_without_observed_peer_goaway'
            else:
                observation = 'error_close_without_observed_peer_goaway'
        rows.append({'connection_id': connection['connection_id'], 'socket_source_id': socket_id,
            'session_source_id': session_id, 'contexts': sorted({b['context'] for b in bound}),
            'binding_status': 'linked' if complete else 'unresolved',
            'initialization_event_indexes': [e['derivative_index'] for e in starts],
            'close_events': [{'derivative_index': e['derivative_index'], 'time': e['time'],
                              'net_error': e['params'].get('net_error')} for e in closes],
            'socket_end_event_indexes': [e['derivative_index'] for e in ends],
            'recv_goaway_event_indexes': [e['derivative_index'] for e in goaways],
            'global_candidate_indexes_during_session': during,
            'observation': observation, 'cause': 'not_proven'})
    return {'gating': False, 'clock': 'netlog_ticks', 'connections': rows,
        'global_candidate_event_indexes': [e['derivative_index'] for e in candidates],
        'summary_truncated': truncated,
        'qualification': 'Candidate windows use inclusive native ticks; equal ticks do not establish ordering. '
            'CERT_VERIFY_PROC_CREATED is proc creation, not proof of a verifier update notification. '
            'Global certificate/config/QUIC pool events have no proven link to an owned NetworkContext. '
            'A final net_error of 0 does not recover the original going-away reason. '
            'No matching event does not prove that no update occurred. No server/CDP clock comparison is made.',
        'additional_chromium_instrumentation_required': [
            'H2 MakeUnavailable/StartGoingAway original error, caller/change type and session source ID',
            'Certificate configuration generation and observer notification linked to NetworkContext/session',
            'SPKI bypass hit and QUIC pool going-away reason as non-sensitive enums/booleans',
            'H2 drain reason as a fixed enum; a finished-going-away reason alone does not prove the trigger']}


def failure_diagnostics(report):
    """Describe failures without contributing evidence to assess()."""
    rows = []
    for run in report.get('runs', []):
        phases = run.get('phases', [])
        for phase in phases:
            wire = phase.get('wire', {})
            connection_id = wire.get('connection_id')
            candidates = [c for c in report.get('connections', []) if c['id'] == connection_id]
            issues, streams = [], []
            if len(candidates) != 1:
                issues.append('missing unique server connection')
            else:
                c = candidates[0]
                matches = [r for r in c['requests'] if dict(r['headers']) == wire.get('headers')]
                streams = [r['stream'] for r in matches]
                if len(matches) != 1:
                    issues.append('missing unique server request binding')
                if phase['phase'] in ('initial', 'resumed'):
                    resumed = phase['phase'] == 'resumed'
                    if c.get('tls') != 'TLSv1.3' or c.get('alpn') != 'h2' or c.get('session_reused') is not resumed:
                        issues.append('negotiated TLS13/H2 session state mismatch')
                    hellos = [h for h in report.get('client_hellos', []) if h.get('connection_id') == connection_id]
                    if len(hellos) != 1:
                        issues.append('missing unique ClientHello binding')
                    elif (41 in hellos[0].get('extensions', [])) is not resumed:
                        issues.append('PSK offer does not match session state')
                    if not c.get('settings') or any(not valid_settings(s) for s in c['settings']):
                        issues.append('no valid HTTP2 peer SETTINGS')
            if issues:
                rows.append({'context': run['context'], 'phase': phase['phase'],
                    'connection_id': connection_id, 'streams': streams, 'issues': issues})
        ids = [p.get('wire', {}).get('connection_id') for p in phases]
        if len(ids) != 5:
            rows.append({'context': run.get('context'), 'phase': 'collection', 'observed_ids': ids})
            continue
        for left, right, equal in ((0, 1, True), (1, 2, True), (2, 3, False), (3, 4, True)):
            if (ids[left] == ids[right]) is not equal:
                rows.append({'context': run['context'], 'phase': PHASES[right], 'previous_phase': PHASES[left],
                    'expected': 'same_connection' if equal else 'new_connection',
                    'observed_ids': [ids[left], ids[right]], 'bindings': [
                        {'phase': phases[index]['phase'], 'connection_id': ids[index],
                         'streams': [request['stream'] for c in report.get('connections', []) if c['id'] == ids[index]
                             for request in c['requests'] if dict(request['headers']) == phases[index]['wire']['headers']]}
                        for index in (left, right)]})
    return rows


def run(browser, headed=False, *, diagnostics_dir=None):
    report = {'schema_version': 1, 'browser_sha256': launch.pool.file_hash(browser),
              'collected_at': datetime.now(timezone.utc).isoformat(), 'errors': [], 'runs': [],
              'client_hellos': [], 'connections': [], 'qualification': {
                  'kind': 'owned TLS endpoint; actual session state and H2 requests',
                  'proxy': 'not_tested', 'dns': 'not_tested', 'physical_network': 'not_attested',
                  'zero_rtt': 'not_tested', 'ticket_contents': 'not_recorded'}}
    netlog = NativeNetLog(diagnostics_dir, report['browser_sha256'])
    report['diagnostics'] = {'netlog': netlog.report, 'server_connections': [],
                             'collection_events': [], 'failures': []}
    try:
        report['diagnostics']['playwright_version'] = version('playwright')
    except PackageNotFoundError:
        report['diagnostics']['playwright_version'] = 'unavailable'
    server, active_context, active_phase = None, None, 'launch'
    try:
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory(prefix='chromix-tls-lifecycle-') as directory:
            try:
                with endpoint(Path(directory), tickets=True) as (server, origin), sync_playwright() as pw:
                    report['diagnostics']['origin'] = origin
                    args = [*launch.NATIVE_ARGS, '--ignore-certificate-errors-spki-list=' + server.spki, *netlog.args]
                    report['launch_args'] = [netlog.redact(a) for a in args]
                    if any(key.upper() == 'SSLKEYLOGFILE' for key in os.environ):
                        raise RuntimeError('SSLKEYLOGFILE must be unset for ticket/key-free diagnostics')
                    instance = pw.chromium.launch(executable_path=str(browser.resolve()), headless=not headed,
                        chromium_sandbox=True, args=args)
                    try:
                        report['browser_version'] = instance.version
                        for i in range(2):
                            active_context, active_phase = i, 'context_create'
                            context = instance.new_context(no_viewport=True)
                            try:
                                page = context.new_page()
                                netlog.attach(context, page, i, origin)
                                active_phase = 'initial'
                                report['diagnostics']['collection_events'].append({'event': 'navigation_started',
                                    'context': i, 'monotonic_ns': time.monotonic_ns()})
                                # Bind the full handshake before navigation can seed a PSK reconnect.
                                response = page.goto(origin + f'/echo?context={i}&phase=initial',
                                    wait_until='load', timeout=30000)
                                if response is None or not response.ok:
                                    raise RuntimeError('missing successful initial navigation response')
                                initial = {'phase': 'initial', 'wire': response.json()}
                                active_phase = 'reuse/goaway/resumed/reuse_after'
                                phases = [initial, *page.evaluate(launch.bounded(PROBE), i)]
                                active_phase = 'identity'
                                report['runs'].append({'context': i, 'phases': phases,
                                    'identity': page.evaluate(launch.bounded(IDENTITY), None)})
                            finally:
                                context.close()
                                report['diagnostics']['collection_events'].append({'event': 'context_closed',
                                    'context': i, 'monotonic_ns': time.monotonic_ns()})
                    finally:
                        instance.close()
                        netlog.report['browser_closed'] = True
                        report['diagnostics']['collection_events'].append({'event': 'browser_closed',
                            'monotonic_ns': time.monotonic_ns()})
            finally:
                # Copy after endpoint joins handlers, even when a browser request failed.
                if server is not None:
                    report['client_hellos'] = copy.deepcopy(server.hellos)
                    report['connections'] = copy.deepcopy(server.connections)
                    report['errors'].extend(server.handshake_errors)
                    report['diagnostics']['server_connections'] = copy.deepcopy(server.connection_diagnostics)
                    report['diagnostics']['dropped_server_connections'] = server.dropped_connection_diagnostics
    except Exception as error:
        report['errors'].append(type(error).__name__ + ': ' + netlog.redact(str(error)))
        report['diagnostics']['failures'].append({'context': active_context, 'phase': active_phase,
                                                 'error_type': type(error).__name__})
    finally:
        netlog.finish(report)
    try:
        report['diagnostics']['failures'].extend(failure_diagnostics(report))
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as error:
        report['diagnostics']['failure_detail_error'] = type(error).__name__
    if launch.pool.file_hash(browser) != report['browser_sha256']:
        report['errors'].append('browser executable changed')
    report['errors'], report['comparisons'] = assess(report)
    report['status'] = 'failed' if report['errors'] else 'passed'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--headed', action='store_true')
    args = parser.parse_args(argv)
    if not args.browser.is_file() or args.output.exists():
        parser.error('use an existing executable and a new report path')
    try:
        directory = diagnostic_directory(args.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    report = run(args.browser, args.headed, diagnostics_dir=directory)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': report['status'], 'errors': report['errors']}))
    return int(report['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())

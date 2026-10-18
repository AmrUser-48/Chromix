"""Diagnostic-only native-log fixtures and owned Python TLS endpoint controls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import ssl
import sys
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fingerprint_transport_audit as transport
import fingerprint_transport_lifecycle_audit as lifecycle
from test_transport_lifecycle import lifecycle_report, native_netlog_fixture


def bound_report():
    report = lifecycle_report()
    report['diagnostics'] = {'origin': 'https://127.0.0.1:1234'}
    responses = [{'context': r['context'], 'url': report['diagnostics']['origin'] + p['wire']['headers'][':path'],
        'socket_source_id': p['wire']['connection_id'] + 70, 'protocol': 'h2'}
        for r in report['runs'] for p in r['phases']]
    return report, responses


def netlog_fixture():
    return lifecycle.project_netlog(native_netlog_fixture(lifecycle_report()))


def write_capture(tmp_path, payload=None, closed=True):
    directory = lifecycle.diagnostic_directory(tmp_path / 'artifact' / 'report.json')
    capture = lifecycle.NativeNetLog(directory, 'a' * 64)
    assert capture.path is not None and not capture.path.is_relative_to(tmp_path / 'artifact')
    data = json.dumps(native_netlog_fixture(lifecycle_report())).encode() if payload is None else payload
    capture.path.write_bytes(data)
    capture.report['browser_closed'] = closed
    report, responses = bound_report()
    capture.report['responses'] = responses
    return capture, report, data


def test_native_summary_binds_independent_socket_and_server_request_ids():
    report, responses = bound_report()
    summary = lifecycle.summarize_netlog(netlog_fixture(), responses, report)
    assert summary['unresolved_bindings'] == 0 and summary['binding_errors'] == []
    assert len(summary['coverage']) == 4
    assert summary['bindings'][0]['server_requests'] == [{'connection_id': 1, 'stream': 1}]
    assert summary['bindings'][0]['socket_source_id'] == 71
    assert summary['bindings'][0]['sources'] == [{'type': 'SOCKET', 'id': 71}, {'type': 'HTTP2_SESSION', 'id': 81}]
    assert summary['events'][-1]['scope'] == 'global_candidate'
    assert all(row['socket_end_count'] == row['session_close_count'] == 1 for row in summary['coverage'])
    assert all(row['goaway_numeric_fields'] == 'not_retained_by_native_redaction' for row in summary['coverage'])


@pytest.mark.parametrize('url', ['https://outside.invalid/secret', 'https://127.0.0.1:12345/echo',
    'https://127.0.0.1:1234/echo?token=secret', 'https://127.0.0.1:1234/echo?context=0&phase=initial&x=1',
    'https://127.0.0.1:1234/echo?context=0&phase=initial&close=1',
    'https://127.0.0.1:1234/echo#secret', 'https://127.0.0.1:1234/private'])
def test_external_or_sensitive_url_is_not_collected(url):
    report, responses = bound_report()
    assert not lifecycle.owned_url(url, report['diagnostics']['origin'])
    responses[0]['url'] = url
    summary = lifecycle.summarize_netlog(netlog_fixture(), responses, report)
    assert all(b['url'] != url for b in summary['bindings'])
    assert summary['binding_errors']


@pytest.mark.parametrize('mutate', [
    lambda v: v['constants'].update(logCaptureMode='Default'),
    lambda v: v['constants']['logEventTypes'].update(EVIL=0),
    lambda v: v['constants']['logEventTypes'].update({'secret.example': 900}),
    lambda v: v['constants']['logEventTypes'].update(EVIL=True),
    lambda v: v['events'][0]['params'].update(net_error={'private': 'secret'}),
    lambda v: v['events'][0]['params'].update(net_error=True),
    lambda v: v['events'][0]['params'].update(timedout='secret'),
    lambda v: v['events'][0]['params'].update(source_dependency={'type': 1, 'id': 'secret'}),
    lambda v: v['events'][0]['source'].update(id=True),
    lambda v: v['events'][0].update(time='private-time'),
    lambda v: v['events'][0].update(type=900),
    lambda v: v.update(events=None),
])
def test_invalid_allowed_values_never_publish_or_claim_capture(tmp_path, mutate):
    value = native_netlog_fixture(lifecycle_report())
    mutate(value)
    capture, report, _ = write_capture(tmp_path, json.dumps(value).encode())
    capture.finish(report)
    assert capture.report['status'] == 'error' and 'derivative' not in capture.report
    assert not list(capture.destination.parent.iterdir()) and not capture.private.exists()


@pytest.mark.parametrize('socket_id', [None, 0, -1, True, 71.5, '71', 999999, float('inf'), float('nan'),
    2**31, 2**32 - 1, 10**1000])
def test_missing_socket_binding_never_becomes_linked(socket_id):
    report, responses = bound_report()
    responses[0]['socket_source_id'] = socket_id
    summary = lifecycle.summarize_netlog(netlog_fixture(), responses, report)
    assert summary['binding_errors']
    assert not any(b['url'] == responses[0]['url'] and b['binding_status'] == 'linked' for b in summary['bindings'])


@pytest.mark.parametrize('source_id', [-2**31, -2**31 + 1, -1, 0, 1, 2**31 - 1])
@pytest.mark.parametrize('field', ['source', 'source_dependency'])
def test_native_wire_id_boundaries_preserve_exact_projection_and_cleanup(tmp_path, field, source_id):
    value = native_netlog_fixture(lifecycle_report())
    event = value['events'][-1]
    if field == 'source':
        event['source']['id'] = source_id
    else:
        event['params']['source_dependency'] = {'type': 0, 'id': source_id, 'secret': 'PRIVATE-dependency'}
    projected = lifecycle.project_netlog(value)
    target = projected['events'][-1]
    assert (target['source'] if field == 'source' else target['params'][field])['id'] == source_id
    assert lifecycle.project_netlog(projected) == projected
    capture, report, data = write_capture(tmp_path, json.dumps(value).encode())
    capture.finish(report)
    assert capture.report['status'] == 'captured' and not capture.report['binding_errors']
    assert capture.report['original']['sha256'] == hashlib.sha256(data).hexdigest()
    assert capture.report['original']['cleanup_status'] == 'deleted' and not capture.private.exists()
    derivative = capture.destination.read_bytes()
    assert b'PRIVATE' not in derivative and str(capture.private) not in json.dumps(capture.report)
    assert lifecycle.strict_json(derivative) == projected
    assert capture.report['derivative']['size_bytes'] == len(derivative)
    assert capture.report['derivative']['sha256'] == hashlib.sha256(derivative).hexdigest()
    snapshot = deepcopy(capture.report)
    capture.finish(report)
    assert capture.report == snapshot and capture.destination.read_bytes() == derivative


@pytest.mark.parametrize('source_id', [-2**31 - 1, 2**31, 2**32 - 1, 2**32, 10**1000,
    True, False, 1.0, -1.0, None, '1', [], {}])
@pytest.mark.parametrize('field', ['source', 'source_dependency'])
def test_invalid_native_wire_ids_reject_full_publication(tmp_path, field, source_id):
    value = native_netlog_fixture(lifecycle_report())
    target = value['events'][0]['source'] if field == 'source' else value['events'][1]['params'][field]
    target['id'] = source_id
    with pytest.raises(ValueError, match='numeric NetLog'):
        lifecycle.project_netlog(value)
    capture, report, data = write_capture(tmp_path, json.dumps(value).encode())
    capture.finish(report)
    assert capture.report['status'] == 'error' and 'derivative' not in capture.report
    assert capture.report['errors'] == ['privacy_projection failed: ValueError']
    assert capture.report['original']['sha256'] == hashlib.sha256(data).hexdigest()
    assert not capture.private.exists() and not capture.destination.exists()
    snapshot = deepcopy(capture.report)
    capture.finish(report)
    assert capture.report == snapshot


@pytest.mark.parametrize('socket_id,valid', [(1, True), (1.0, True), (2**31 - 1, True),
    (float(2**31 - 1), True), (0, False), (-1, False), (-2**31, False), (2**31, False),
    (2**32 - 1, False), (10**1000, False), (True, False), (False, False), (1.5, False),
    (float('inf'), False), (float('-inf'), False), (float('nan'), False), ('1', False),
    (None, False), ([], False), ({}, False)])
def test_cdp_socket_domain_is_separate_from_native_wire_ids(tmp_path, socket_id, valid):
    capture, report, _ = write_capture(tmp_path)
    callbacks = {}
    session = SimpleNamespace(on=lambda name, callback: callbacks.update({name: callback}),
        send=lambda method: {'arguments': capture.args} if method == 'Browser.getBrowserCommandLine' else {})
    capture.report['responses'] = []
    origin = report['diagnostics']['origin']
    capture.attach(SimpleNamespace(new_cdp_session=lambda page: session), None, 0, origin)
    callbacks['Network.responseReceived']({'response': {'url': origin + '/echo',
        'connectionId': socket_id, 'protocol': 'h2'}})
    if valid:
        assert lifecycle.cdp_socket_id(socket_id) == int(socket_id)
        assert capture.report['responses'][0]['socket_source_id'] == int(socket_id)
        assert type(capture.report['responses'][0]['socket_source_id']) is int
    else:
        with pytest.raises(ValueError, match='CDP socket ID'):
            lifecycle.cdp_socket_id(socket_id)
        assert not capture.report['responses'] and capture.report['errors']
    capture.finish(report)
    assert not capture.private.exists()


@pytest.mark.parametrize('source_id', [-2**31, -1, 2**31 - 1])
def test_signed_native_session_ids_link_without_rewriting(tmp_path, source_id):
    value = native_netlog_fixture(lifecycle_report())
    for event in value['events']:
        if event['source'] == {'type': 2, 'id': 81, 'start_time': '100'}:
            event['source']['id'] = source_id
    capture, report, _ = write_capture(tmp_path, json.dumps(value).encode())
    capture.finish(report)
    assert capture.report['status'] == 'captured'
    assert capture.report['bindings'][0]['session_source_id'] == source_id
    assert capture.report['coverage'][0]['session_source_id'] == source_id
    assert not capture.private.exists()


@pytest.mark.parametrize('socket_id', [1, 2**31 - 1])
def test_supported_cdp_boundaries_match_native_socket_and_dependency_exactly(tmp_path, socket_id):
    value = native_netlog_fixture(lifecycle_report())
    for event in value['events']:
        if event['source']['type'] == 1 and event['source']['id'] == 71:
            event['source']['id'] = socket_id
        dependency = event['params'].get('source_dependency', {})
        if dependency.get('id') == 71:
            dependency['id'] = socket_id
    capture, report, _ = write_capture(tmp_path, json.dumps(value).encode())
    for response in capture.report['responses']:
        if response['socket_source_id'] == 71:
            response['socket_source_id'] = float(socket_id)
    capture.finish(report)
    assert capture.report['status'] == 'captured' and not capture.report['binding_errors']
    assert capture.report['bindings'][0]['socket_source_id'] == socket_id
    assert not capture.private.exists()


@pytest.mark.parametrize('mode', ['both', 'dependency_only'])
def test_negative_native_socket_ids_do_not_alias_positive_cdp_ids(tmp_path, mode):
    value = native_netlog_fixture(lifecycle_report())
    for event in value['events']:
        if mode == 'both' and event['source']['type'] == 1 and event['source']['id'] == 71:
            event['source']['id'] = -71
        dependency = event['params'].get('source_dependency', {})
        if dependency.get('id') == 71:
            dependency['id'] = -71
    projected = lifecycle.project_netlog(value)
    assert lifecycle.project_netlog(projected) == projected
    capture, report, _ = write_capture(tmp_path, json.dumps(value).encode())
    capture.finish(report)
    assert capture.report['status'] == 'partial'
    assert capture.report['bindings'][0]['binding_status'] == 'unresolved'
    assert not capture.private.exists()


@pytest.mark.parametrize('mutate', [
    lambda v: v['constants']['logEventTypes'].update(BAD=-1),
    lambda v: v['constants']['logEventTypes'].update(BAD=65536),
    lambda v: v['events'][0].update(type=-1),
    lambda v: v['events'][0].update(phase=-1),
    lambda v: v['events'][0]['source'].update(type=-1),
    lambda v: v['events'][1]['params']['source_dependency'].update(type=-1),
    lambda v: v['events'][0]['params'].update(byte_count=-1),
    lambda v: v['events'][0]['params'].update(byte_count=2**31),
    lambda v: v['events'][0]['params'].update(net_error=-2**31 - 1),
    lambda v: v['events'][0]['params'].update(os_error=2**31),
    lambda v: v['events'][0].update(time='-1'),
    lambda v: v['events'][0]['source'].update(start_time=str(2**63)),
])
def test_signed_id_amendment_does_not_relax_other_projection_fields(tmp_path, mutate):
    value = native_netlog_fixture(lifecycle_report())
    mutate(value)
    capture, report, _ = write_capture(tmp_path, json.dumps(value).encode())
    capture.finish(report)
    assert capture.report['status'] == 'error' and 'derivative' not in capture.report
    assert not capture.private.exists() and not capture.destination.exists()


def test_whole_native_envelope_is_projected_hash_bound_and_original_deleted(tmp_path):
    value = native_netlog_fixture(lifecycle_report())
    value['unknown'] = {'ticket': 'PRIVATE-envelope'}
    value['constants']['unknown'] = {'private_key': 'PRIVATE-key'}
    value['events'][0]['params'].update(bytes='PRIVATE-bytes', headers=['PRIVATE-auth'],
                                       unknown={'nested': 'PRIVATE-nested'})
    value['events'][0]['secret'] = 'PRIVATE-event'
    value['events'][0]['source']['secret'] = 'PRIVATE-source'
    value['events'][1]['params']['source_dependency']['secret'] = 'PRIVATE-dependency'
    capture, report, data = write_capture(tmp_path, json.dumps(value).encode())
    private = capture.private
    capture.finish(report)
    assert capture.report['status'] == 'captured' and capture.report['errors'] == []
    assert not private.exists()
    assert capture.report['original'] == {'retention': 'ephemeral_private', 'read_status': 'hashed',
        'cleanup_status': 'deleted', 'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    derivative = capture.destination.read_bytes()
    assert b'PRIVATE' not in derivative and derivative != data
    assert capture.report['derivative']['sha256'] == hashlib.sha256(derivative).hexdigest()
    assert capture.report['derivative']['format'] == 'chromium-netlog-privacy-projection-v1'
    assert not {'path', 'sha256', 'size_bytes'} & capture.report.keys()
    lifecycle.validate_netlog_projection(lifecycle.strict_json(derivative))
    assert len(list((tmp_path / 'artifact').rglob('*.*'))) == 1
    assert str(private) not in json.dumps(capture.report)


@pytest.mark.parametrize('payload', [b'{"events": [', b'{}', b'null', b'[]', b'{"x":1,"x":2}',
    b'{"unapproved":NaN}', b'{"unapproved":Infinity}', b'{"unapproved":1e999}'])
def test_unflushed_or_malformed_capture_deleted_without_derivative(tmp_path, payload):
    capture, report, data = write_capture(tmp_path, payload)
    capture.finish(report)
    assert capture.report['status'] == 'error' and capture.report['errors']
    assert capture.report['original']['sha256'] == hashlib.sha256(data).hexdigest()
    assert not capture.private.exists() and not capture.destination.exists()


def test_browser_close_incomplete_does_not_publish(tmp_path):
    capture, report, _ = write_capture(tmp_path, closed=False)
    capture.finish(report)
    assert capture.report['status'] == 'error'
    assert not capture.private.exists() and not capture.destination.exists()


def test_capture_file_size_bound_and_cleanup(tmp_path, monkeypatch):
    capture, report, _ = write_capture(tmp_path, b'x' * 33)
    monkeypatch.setattr(lifecycle, 'NETLOG_READ_LIMIT', 32)
    capture.finish(report)
    assert capture.report['status'] == 'error' and 'sha256' not in capture.report['original']
    assert capture.report['original']['size_bytes'] == 33
    assert capture.report['original']['read_status'] == 'oversized'
    assert not capture.private.exists() and not capture.destination.exists()


def test_private_path_is_outside_workdir_artifact_and_source(tmp_path, monkeypatch):
    workdir = tmp_path / 'workdir'
    directory = lifecycle.diagnostic_directory(workdir / 'fingerprint-diagnostics' / 'run' / 'raw.json')
    root = tmp_path / 'runner-temp'
    root.mkdir()
    monkeypatch.setenv('RUNNER_TEMP', str(root))
    capture = lifecycle.NativeNetLog(directory, 'a' * 64)
    assert capture.private.parent == root and not capture.private.is_relative_to(workdir)
    capture.finish({})
    assert not capture.private.exists() and capture.report['original']['cleanup_status'] == 'deleted'
    monkeypatch.setenv('RUNNER_TEMP', str(workdir))
    monkeypatch.setattr(lifecycle.tempfile, 'gettempdir', lambda: str(directory))
    blocked = lifecycle.NativeNetLog(directory, 'a' * 64)
    assert blocked.path is None and blocked.report['status'] == 'unavailable'


def test_private_root_alias_into_workdir_is_rejected(tmp_path, monkeypatch):
    workdir = tmp_path / 'workdir'
    directory = lifecycle.diagnostic_directory(workdir / 'fingerprint-diagnostics' / 'raw.json')
    child = workdir / 'child'
    child.mkdir()
    alias = str(child / '..')
    monkeypatch.setenv('RUNNER_TEMP', alias)
    monkeypatch.setattr(lifecycle.tempfile, 'gettempdir', lambda: alias)
    capture = lifecycle.NativeNetLog(directory, 'a' * 64)
    assert capture.path is None and capture.report['status'] == 'unavailable'


def test_original_symlink_and_derivative_write_failure_remain_outside_upload(tmp_path, monkeypatch):
    capture, report, _ = write_capture(tmp_path)
    capture.path.unlink()
    outside = tmp_path / 'not-capture.txt'
    outside.write_text('PRIVATE-target')
    capture.path.symlink_to(outside)
    capture.finish(report)
    assert capture.report['status'] == 'error' and not capture.private.exists()
    assert outside.read_text() == 'PRIVATE-target' and not capture.destination.exists()
    other, report, _ = write_capture(tmp_path)
    original_open = Path.open
    def blocked_open(path, *args, **kwargs):
        if path == other.destination:
            raise PermissionError(str(other.private))
        return original_open(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', blocked_open)
        other.finish(report)
    assert other.report['status'] == 'error' and not other.private.exists()
    assert not other.destination.exists() and str(other.private) not in json.dumps(other.report)


def test_cdp_response_callback_rejects_nonfinite_and_arbitrary_values(tmp_path):
    directory = lifecycle.diagnostic_directory(tmp_path / 'artifact' / 'report.json')
    capture = lifecycle.NativeNetLog(directory, 'a' * 64)
    callbacks = {}
    session = SimpleNamespace(on=lambda event, callback: callbacks.update({event: callback}),
        send=lambda method: {'arguments': capture.args} if method == 'Browser.getBrowserCommandLine' else {})
    context = SimpleNamespace(new_cdp_session=lambda page: session)
    origin = 'https://127.0.0.1:1234'
    capture.attach(context, None, 0, origin)
    for socket_id, protocol in ((float('nan'), 'h2'), ({'secret': 'PRIVATE'}, 'h2'), (71, 'PRIVATE')):
        callbacks['Network.responseReceived']({'response': {'url': origin + '/echo',
            'connectionId': socket_id, 'protocol': protocol}})
    assert capture.report['responses'] == [] and capture.report['errors']
    assert 'PRIVATE' not in json.dumps(capture.report, allow_nan=False)
    assert str(capture.private) not in json.dumps(capture.report)
    capture.finish({})
    assert not capture.private.exists()


def test_cleanup_error_does_not_leak_private_path_or_claim_captured(tmp_path, monkeypatch):
    capture, report, _ = write_capture(tmp_path)
    def fail(path):
        raise PermissionError(str(path))
    with monkeypatch.context() as patch:
        patch.setattr(lifecycle.shutil, 'rmtree', fail)
        capture.finish(report)
    assert capture.report['status'] == 'partial'
    assert capture.report['original']['cleanup_status'] == 'error'
    assert str(capture.private) not in json.dumps(capture.report)
    assert not capture.path.is_relative_to(capture.destination.parent)
    capture.cleanup()
    assert not capture.private.exists()


def test_diagnostic_paths_reject_reuse_and_symlink_escape(tmp_path):
    directory = lifecycle.diagnostic_directory(tmp_path / 'raw.json')
    assert directory.parent == tmp_path
    assert directory != lifecycle.diagnostic_directory(tmp_path / 'raw.json')
    (directory / 'occupied').write_text('x')
    assert lifecycle.NativeNetLog(directory, 'a' * 64).report['status'] == 'unavailable'
    assert lifecycle.NativeNetLog(None, 'a' * 64).report['errors']
    link = tmp_path / 'escape'
    try:
        link.symlink_to(directory, target_is_directory=True)
    except OSError:
        pytest.skip('symlinks unavailable')
    with pytest.raises(ValueError, match='links'):
        lifecycle.diagnostic_directory(link / 'raw.json')
    assert lifecycle.NativeNetLog(link, 'a' * 64).report['errors']


@pytest.mark.parametrize('remove', ['HTTP2_SESSION_INITIALIZED', 'HTTP2_SESSION_CLOSE',
    'HTTP2_SESSION_RECV_GOAWAY', 'SOCKET_ALIVE'])
def test_missing_chain_or_terminal_coverage_is_partial(tmp_path, remove):
    value = native_netlog_fixture(lifecycle_report())
    value['events'] = [e for e in value['events'] if e['type'] != value['constants']['logEventTypes'][remove]]
    capture, report, _ = write_capture(tmp_path, json.dumps(value).encode())
    capture.finish(report)
    assert capture.report['status'] == 'partial' and capture.report['binding_errors']
    assert capture.report['unresolved_bindings'] > 0


@pytest.mark.parametrize('mode', ['socket_only', 'one_socket', 'duplicate_edge', 'two_sessions',
    'session_two_sockets', 'connection_two_sockets', 'duplicate_url', 'bad_order', 'cross_context',
    'duplicate_close', 'missing_close_reason', 'wrong_protocol', 'unexpected_goaway'])
def test_reviewer_false_completeness_and_contradictory_chains_are_partial(tmp_path, mode):
    value = native_netlog_fixture(lifecycle_report())
    capture, report, _ = write_capture(tmp_path)
    if mode == 'socket_only':
        value['events'] = [e for e in value['events'] if e['type'] == 0]
    elif mode == 'one_socket':
        for response in capture.report['responses']:
            response['socket_source_id'] = 71
    elif mode in ('duplicate_edge', 'two_sessions', 'session_two_sockets'):
        edge = deepcopy(value['events'][1])
        if mode == 'two_sessions':
            edge['source']['id'] = 999
        elif mode == 'session_two_sockets':
            edge['params']['source_dependency']['id'] = 72
        value['events'].append(edge)
    elif mode == 'connection_two_sockets':
        capture.report['responses'][1]['socket_source_id'] = 72
    elif mode == 'duplicate_url':
        capture.report['responses'].append(deepcopy(capture.report['responses'][0]))
    elif mode == 'duplicate_close':
        value['events'].append(deepcopy(next(e for e in value['events'] if e['type'] == 2)))
    elif mode == 'missing_close_reason':
        next(e for e in value['events'] if e['type'] == 2)['params'].clear()
    elif mode == 'wrong_protocol':
        capture.report['responses'][0]['protocol'] = 'http/1.1'
    elif mode == 'unexpected_goaway':
        event = deepcopy(next(e for e in value['events'] if e['type'] == 3))
        event['source']['id'] = 82
        value['events'].append(event)
    elif mode == 'cross_context':
        capture.report['responses'].append({**deepcopy(capture.report['responses'][0]), 'context': 1})
        report['runs'][1]['phases'][0]['wire']['connection_id'] = 1
    else:
        next(e for e in value['events'] if e['type'] == 2)['time'] = '1'
    expected_assessment = lifecycle.assess(report)
    capture.path.write_text(json.dumps(value))
    capture.finish(report)
    assert capture.report['status'] == 'partial' and capture.report['binding_errors']
    assert capture.report['unresolved_bindings'] > 0
    assert not capture.private.exists()
    assert lifecycle.assess(report) == expected_assessment


@pytest.mark.parametrize('inject', [lambda v: v.update(polledData={}),
    lambda v: v['constants'].update(clientInfo={}), lambda v: v['events'][0].update(secret='x'),
    lambda v: v['events'][0]['source'].update(secret='x'),
    lambda v: v['events'][0]['params'].update(bytes='secret')])
def test_unsanitized_derivative_shape_is_rejected(inject):
    value = netlog_fixture()
    inject(value)
    with pytest.raises(ValueError, match='exact privacy projection'):
        lifecycle.validate_netlog_projection(value)


def replacement_report():
    report = lifecycle_report()
    extra = deepcopy(report['connections'][0])
    extra.update(id=5, session_reused=True, requests=extra['requests'][1:])
    report['connections'][0]['requests'] = report['connections'][0]['requests'][:1]
    report['connections'][0].pop('goaway_stream')
    report['connections'].append(extra)
    hello = deepcopy(report['client_hellos'][0])
    hello.update(connection_id=5)
    hello['extensions'].append(41)
    report['client_hellos'].append(hello)
    for p in report['runs'][0]['phases'][1:3]:
        p['wire']['connection_id'] = 5
    return report


@pytest.mark.parametrize('mode', ['connection', 'headers', 'extra_header', 'missing_header',
    'bool_connection', 'float_connection', 'phase_name', 'phase_path'])
def test_phase_response_requires_exact_phase_connection_and_full_headers(tmp_path, mode):
    capture, report, _ = write_capture(tmp_path)
    phase = report['runs'][0]['phases'][0]
    if mode == 'connection':
        phase['wire']['connection_id'] = 2
    elif mode == 'headers':
        phase['wire']['headers'][':method'] = 'POST'
    elif mode == 'extra_header':
        phase['wire']['headers']['x-unobserved'] = 'synthetic'
    elif mode == 'missing_header':
        phase['wire']['headers'].pop(':authority')
    elif mode == 'bool_connection':
        phase['wire']['connection_id'] = True
    elif mode == 'float_connection':
        phase['wire']['connection_id'] = 1.0
    elif mode == 'phase_name':
        phase['phase'] = 'reuse'
    else:
        phase['wire']['headers'][':path'] = report['runs'][0]['phases'][1]['wire']['headers'][':path']
    assessment = lifecycle.assess(report)
    assert assessment[0]
    capture.finish(report)
    assert capture.report['status'] == 'partial' and capture.report['binding_errors']
    assert capture.report['bindings'][0]['binding_status'] == 'unresolved'
    assert not capture.private.exists() and lifecycle.assess(report) == assessment


@pytest.mark.parametrize('kind', ['identity', 'favicon'])
@pytest.mark.parametrize('mode', ['valid', 'duplicate', 'unresolved', 'wrong_protocol',
    'cross_context', 'wrong_connection', 'wrong_headers'])
def test_every_nonphase_response_requires_consistent_owned_binding(tmp_path, kind, mode):
    capture, report, _ = write_capture(tmp_path)
    wire = report['runs'][0]['identity']['wire']
    path = '/echo' if kind == 'identity' else '/favicon.ico'
    if kind == 'favicon':
        headers = [[k, path if k == ':path' else v] for k, v in wire['headers'].items()]
        report['connections'][1]['requests'].append({'stream': 9, 'headers': headers})
    response = {'context': 0, 'url': report['diagnostics']['origin'] + path,
        'socket_source_id': 72, 'protocol': 'h2'}
    capture.report['responses'].append(response)
    if mode == 'duplicate':
        capture.report['responses'].append(deepcopy(response))
    elif mode == 'unresolved':
        response['socket_source_id'] = 999
    elif mode == 'wrong_protocol':
        response['protocol'] = 'http/1.1'
    elif mode == 'cross_context':
        response['context'] = 1
    elif mode == 'wrong_connection':
        if kind == 'identity':
            wire['connection_id'] = 1
        else:
            request = report['connections'][1]['requests'].pop()
            report['connections'][3]['requests'].append(request)
    elif mode == 'wrong_headers':
        if kind == 'identity':
            wire['headers'][':method'] = 'POST'
        else:
            # Favicon has no echoed wire; only its path and owned connection bind it.
            report['connections'][1]['requests'][-1]['headers'] = [
                [k, '/unowned' if k == ':path' else v]
                for k, v in report['connections'][1]['requests'][-1]['headers']]
    capture.finish(report)
    if mode == 'valid':
        assert capture.report['status'] == 'captured' and not capture.report['binding_errors']
        assert capture.report['bindings'][-1]['server_requests'][0]['connection_id'] == 2
    else:
        assert capture.report['status'] == 'partial' and capture.report['binding_errors']
        assert capture.report['bindings'][-1]['binding_status'] == 'unresolved'
    assert not capture.private.exists()


@pytest.mark.parametrize('mode', ['nonowned', 'favicon_unresolved', 'no_context_wires'])
def test_ops_f1_additional_unresolved_responses_prevent_capture(tmp_path, mode):
    capture, report, _ = write_capture(tmp_path)
    response = {'context': 0, 'url': report['diagnostics']['origin'] + '/favicon.ico',
        'socket_source_id': 71, 'protocol': 'h2'}
    if mode == 'nonowned':
        response['url'] = 'https://outside.invalid/echo'
    elif mode == 'no_context_wires':
        report['runs'][0]['phases'] = []
        report['runs'][0]['identity'] = {}
        report['connections'][0]['requests'].append({'stream': 9, 'headers': [[':path', '/favicon.ico']]})
    capture.report['responses'].append(response)
    capture.finish(report)
    assert capture.report['status'] == 'partial' and capture.report['binding_errors']
    assert not capture.private.exists()


def test_accurately_bound_initial_replacement_can_be_captured_while_acceptance_fails(tmp_path):
    report = replacement_report()
    report['diagnostics'] = {'origin': 'https://127.0.0.1:1234'}
    capture, _, _ = write_capture(tmp_path, json.dumps(native_netlog_fixture(report)).encode())
    capture.report['responses'] = [{'context': run['context'],
        'url': report['diagnostics']['origin'] + phase['wire']['headers'][':path'],
        'socket_source_id': 70 + phase['wire']['connection_id'], 'protocol': 'h2'}
        for run in report['runs'] for phase in run['phases']]
    assessment = lifecycle.assess(report)
    assert assessment[0] == ['HTTP2 reuse / GOAWAY reconnect sequence failed']
    capture.finish(report)
    assert capture.report['status'] == 'captured' and not capture.report['binding_errors']
    assert len(capture.report['coverage']) == 5 and capture.report['unresolved_bindings'] == 0
    assert not capture.report['summary_limit_reached'] and not capture.private.exists()
    assert capture.report['bindings'][0]['server_requests'] == [{'connection_id': 1, 'stream': 1}]
    assert capture.report['bindings'][1]['server_requests'] == [{'connection_id': 5, 'stream': 3}]
    report['diagnostics']['netlog'] = capture.report
    assert lifecycle.assess(report) == assessment


@pytest.mark.parametrize('status', ['captured', 'partial', 'error', 'unavailable'])
def test_original_navigation_replacement_remains_failed_regardless_of_diagnostics(status):
    report = replacement_report()
    report.update(status='passed', diagnostics={'netlog': {'status': status, 'errors': []}})
    assert lifecycle.assess(report)[0] == ['HTTP2 reuse / GOAWAY reconnect sequence failed']
    assert lifecycle.failure_diagnostics(report) == [{'context': 0, 'phase': 'reuse',
        'previous_phase': 'initial', 'expected': 'same_connection', 'observed_ids': [1, 5],
        'bindings': [{'phase': 'initial', 'connection_id': 1, 'streams': [1]},
                     {'phase': 'reuse', 'connection_id': 5, 'streams': [3]}]}]


@pytest.mark.parametrize('mutate', [
    lambda c: c.update(settings=[]), lambda c: c.update(tls='TLSv1.2'),
    lambda c: c.update(alpn='http/1.1'), lambda c: c.update(session_reused=False),
    lambda c: c['requests'][1].update(stream=c['requests'][0]['stream']),
])
def test_bad_replacement_connection_cannot_escape_existing_reuse_gate(mutate):
    report = replacement_report()
    mutate(report['connections'][-1])
    assert lifecycle.assess(report)[0]


def test_context_phase_session_diagnostics_do_not_change_assessment():
    report = lifecycle_report()
    report['connections'][3]['session_reused'] = False
    before = lifecycle.assess(report)
    detail = lifecycle.failure_diagnostics(report)
    assert detail == [{'context': 1, 'phase': 'resumed', 'connection_id': 4, 'streams': [1],
                       'issues': ['negotiated TLS13/H2 session state mismatch']}]
    assert lifecycle.assess(report) == before


def assert_order(record, *names):
    events = [event['event'] for event in record['events']]
    assert [events.index(name) for name in names] == sorted(events.index(name) for name in names)
    times = [event['monotonic_ns'] for event in record['events']]
    assert times == sorted(times)
    assert events[-1] == 'closed'


@pytest.mark.parametrize('mode,reason', [('eof', 'peer_eof'), ('goaway', 'peer_goaway'),
                                         ('timeout', 'timeout'), ('exception', 'exception')])
def test_real_owned_endpoint_close_ordering(tmp_path, monkeypatch, mode, reason):
    pytest.importorskip('h2')
    from h2.config import H2Configuration
    from h2.connection import H2Connection
    if mode == 'timeout':
        original = ssl.SSLSocket.settimeout
        def short_timeout(self, value):
            return original(self, 0.05 if self.server_side else value)
        monkeypatch.setattr(ssl.SSLSocket, 'settimeout', short_timeout)
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.check_hostname = False
    client.verify_mode = ssl.CERT_NONE
    client.set_alpn_protocols(['h2'])
    with transport.endpoint(tmp_path, tickets=True) as (server, origin):
        address = urlsplit(origin)
        with socket.create_connection((address.hostname, address.port), timeout=5) as raw:
            with client.wrap_socket(raw, server_hostname=address.hostname) as connection:
                h2 = H2Connection(H2Configuration(client_side=True))
                h2.initiate_connection()
                connection.sendall(h2.data_to_send())
                from h2.events import SettingsAcknowledged
                acknowledged = False
                while not acknowledged:
                    data = connection.recv(65536)
                    assert data
                    acknowledged = any(isinstance(e, SettingsAcknowledged) for e in h2.receive_data(data))
                if mode == 'goaway':
                    h2.close_connection(error_code=0, last_stream_id=0)
                    connection.sendall(h2.data_to_send())
                elif mode == 'exception':
                    # A SETTINGS frame on a nonzero stream is a protocol error.
                    connection.sendall(b'\x00\x00\x00\x04\x00\x00\x00\x00\x01')
                if mode != 'eof':
                    while connection.recv(65536):
                        pass
    record = server.connection_diagnostics[0]
    assert record['close_reason'] == reason
    assert_order(record, 'accepted', 'tls_handshake_started', 'tls_handshake_completed', reason, 'closed')
    assert not server.dropped_connection_diagnostics


def test_real_goaway_send_failure_does_not_claim_flushed_response(tmp_path, monkeypatch):
    pytest.importorskip('h2')
    from h2.config import H2Configuration
    from h2.connection import H2Connection
    original = ssl.SSLSocket.sendall
    def fail_goaway(self, data, *args, **kwargs):
        if self.server_side and len(data) >= 17 and data[-17:-13] == b'\x00\x00\x08\x07':
            raise ConnectionResetError('synthetic send failure')
        return original(self, data, *args, **kwargs)
    monkeypatch.setattr(ssl.SSLSocket, 'sendall', fail_goaway)
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.check_hostname = False
    client.verify_mode = ssl.CERT_NONE
    client.set_alpn_protocols(['h2'])
    with transport.endpoint(tmp_path, tickets=True) as (server, origin):
        address = urlsplit(origin)
        with socket.create_connection((address.hostname, address.port), timeout=5) as raw:
            with client.wrap_socket(raw, server_hostname=address.hostname) as connection:
                h2 = H2Connection(H2Configuration(client_side=True))
                h2.initiate_connection()
                h2.send_headers(1, [(':method', 'GET'), (':authority', address.netloc),
                    (':scheme', 'https'), (':path', '/echo?context=0&phase=goaway&close=1')], end_stream=True)
                connection.sendall(h2.data_to_send())
                while connection.recv(65536):
                    pass
    record = server.connection_diagnostics[0]
    assert_order(record, 'request_received', 'response_queued', 'goaway_queued', 'exception', 'closed')
    assert record['close_reason'] == 'exception'
    assert not any(e['event'] in ('goaway_sent', 'response_sent') for e in record['events'])
    assert server.connections[0]['goaway_stream'] == 1
    report = lifecycle_report()
    report['connections'][0]['requests'].clear()
    report['diagnostics'] = {'server_connections': server.connection_diagnostics}
    assert lifecycle.assess(report)[0]


def fake_handler(error, *, handshake=False):
    class Socket:
        session_reused = False
        def settimeout(self, timeout):
            assert timeout == 10
        def getsockname(self):
            return ('127.0.0.1', 1234)
        def do_handshake(self):
            if handshake:
                raise error
        def version(self):
            return 'TLSv1.3'
        def selected_alpn_protocol(self):
            return 'h2'
        def close(self):
            pass
    connection = Socket()
    server = SimpleNamespace(next_id=1, lock=threading.Lock(), connection_ids={},
        connection_diagnostics=[], dropped_connection_diagnostics=0,
        tls=SimpleNamespace(wrap_socket=lambda *a, **kw: connection))
    handler = transport.Handler.__new__(transport.Handler)
    handler.server, handler.request, handler.client_address = server, connection, ('127.0.0.1', 4567)
    def fail():
        handler.stage = 'http2_response_send'
        handler.event('response_queued', stream=5)
        handler.event('goaway_queued', last_stream_id=5, error_code=0)
        raise error
    handler.handle_http2 = fail
    return handler, server


@pytest.mark.parametrize('error', [TimeoutError(), ConnectionResetError(), ssl.SSLError(), ValueError()])
@pytest.mark.parametrize('handshake', [False, True])
def test_handshake_and_send_failure_never_claim_goaway_sent(error, handshake):
    handler, server = fake_handler(error, handshake=handshake)
    if isinstance(error, ValueError):
        with pytest.raises(ValueError):
            handler.handle()
    else:
        handler.handle()
    record = server.connection_diagnostics[0]
    reason = 'timeout' if isinstance(error, TimeoutError) else 'exception'
    assert record['close_reason'] == reason
    assert_order(record, 'accepted', 'tls_handshake_started', reason, 'closed')
    assert not any(e['event'] == 'goaway_sent' for e in record['events'])
    assert not server.connection_ids


def test_socket_linking_does_not_walk_shared_sources_into_unowned_sessions():
    value = netlog_fixture()
    value['events'].append({'type': 1, 'phase': 0, 'time': '100', 'source': {'type': 2, 'id': 9999},
        'params': {'source_dependency': {'type': 2, 'id': 81}}})
    report, responses = bound_report()
    summary = lifecycle.summarize_netlog(value, responses, report)
    assert not any(e['source']['id'] == 9999 for e in summary['events'])
    assert not lifecycle.owned_url('https://outside.invalid/echo', 'https://outside.invalid')


def test_close_exception_still_records_terminal_event_and_releases_binding():
    handler, server = fake_handler(ConnectionResetError())
    def close():
        raise OSError('synthetic close failure')
    handler.request.close = close
    with pytest.raises(OSError, match='synthetic close'):
        handler.handle()
    record = server.connection_diagnostics[0]
    assert_order(record, 'exception', 'close_exception', 'closed')
    assert record['close_reason'] == 'close_exception'
    assert not server.connection_ids


def test_server_event_buffer_is_bounded_and_close_remains_visible():
    handler, _ = fake_handler(ValueError())
    handler.diagnostic = {'events': [], 'dropped_events': 0}
    for _ in range(300):
        handler.event('request_received', stream=1)
    handler.event('closed', reason='peer_eof')
    assert len(handler.diagnostic['events']) == 256
    assert handler.diagnostic['dropped_events'] == 45
    assert handler.diagnostic['events'][-1]['event'] == 'closed'


def certificate_update_fixture():
    report = replacement_report()
    report['diagnostics'] = {'origin': 'https://127.0.0.1:1234'}
    value = native_netlog_fixture(report)
    value['constants']['logSourceType'].update(CERT_VERIFY_PROC=3, QUIC_SESSION_POOL=4)
    names = value['constants']['logEventTypes']
    for name in (*sorted(lifecycle.NETLOG_GLOBAL_LIFECYCLE_EVENTS), 'CERT_VERIFY_PROC',
                 'CERT_VERIFY_PROC_CREATED_UNAPPROVED'):
        names[name] = len(names)
    close = next(e for e in value['events'] if e['type'] == names['HTTP2_SESSION_CLOSE'])
    close['params']['net_error'] = 0
    rows = [
        ('CERT_VERIFY_PROC_CREATED', 3, -2**31 + 4, '99'),
        ('CERT_VERIFY_PROC_CREATED', 3, -2**31 + 3, '101'),
        ('QUIC_SESSION_POOL_MARK_ALL_ACTIVE_SESSIONS_GOING_AWAY', 4, 18, '101'),
        ('QUIC_SESSION_POOL_CLOSE_ALL_SESSIONS', 4, 11, '102'),
        ('CERT_VERIFY_PROC_CREATED', 3, -2**31 + 2, '103'),
        ('CERT_VERIFY_PROC', 3, 999, '101'),
        ('CERT_VERIFY_PROC_CREATED_UNAPPROVED', 3, 998, '101'),
    ]
    for name, kind, sid, tick in rows:
        value['events'].append({'type': names[name], 'phase': 0, 'time': tick,
            'source': {'type': kind, 'id': sid}, 'params': {
                'certificates': ['PRIVATE-cert'], 'host': 'PRIVATE-host', 'spki': 'PRIVATE-spki',
                'description': 'PRIVATE-description', 'reason': 'PRIVATE-reason',
                'headers': ['PRIVATE-header'], 'bytes': 'PRIVATE-bytes',
                'network_context': 'PRIVATE-context', 'generation': 'PRIVATE-generation',
                'unknown': {'nested': 'PRIVATE-secret'}}})
    responses = [{'context': r['context'], 'url': report['diagnostics']['origin'] + p['wire']['headers'][':path'],
        'socket_source_id': p['wire']['connection_id'] + 70, 'protocol': 'h2'}
        for r in report['runs'] for p in r['phases']]
    return value, report, responses


def test_certificate_pool_events_are_private_temporal_candidates_not_close_causes(tmp_path):
    value, report, responses = certificate_update_fixture()
    assessment = lifecycle.assess(report)
    capture, _, _ = write_capture(tmp_path, json.dumps(value).encode())
    capture.report['responses'] = responses
    capture.finish(report)
    assert capture.report['status'] == 'captured' and not capture.report['gating']
    derivative = capture.destination.read_bytes()
    assert b'PRIVATE' not in derivative and 'PRIVATE' not in json.dumps(capture.report)
    projected = lifecycle.strict_json(derivative)
    assert lifecycle.project_netlog(projected) == projected
    assert not capture.private.exists()
    summary = capture.report
    added = [e for e in summary['events'] if e['type'] in lifecycle.NETLOG_GLOBAL_LIFECYCLE_EVENTS]
    assert len(added) == 5 and all(e['scope'] == 'global_candidate' for e in added)
    assert all(e['params'] == {} for e in added)
    assert any(e['source']['id'] == -2**31 + 3 for e in added)
    assert not any(e['type'] in ('CERT_VERIFY_PROC', 'CERT_VERIFY_PROC_CREATED_UNAPPROVED')
                   for e in summary['events'])
    analysis = summary['close_analysis']
    assert not analysis['gating'] and analysis['clock'] == 'netlog_ticks'
    c1 = analysis['connections'][0]
    assert c1['connection_id'] == 1 and c1['contexts'] == [0]
    assert c1['binding_status'] == 'linked'
    assert c1['observation'] == 'ok_close_without_observed_peer_goaway'
    assert c1['cause'] == 'not_proven' and c1['recv_goaway_event_indexes'] == []
    assert c1['close_events'] == [{'derivative_index': 2, 'time': '102', 'net_error': 0}]
    assert c1['initialization_event_indexes'] == [1] and c1['socket_end_event_indexes'] == [3]
    window = c1['global_candidate_indexes_during_session']
    assert window == [e['derivative_index'] for e in added if e['time'] in ('101', '102')]
    assert len(window) == 3
    assert all(i in analysis['global_candidate_event_indexes'] for i in window)
    assert all(projected['events'][e['derivative_index']]['time'] == e['time'] for e in added)
    assert 'inclusive native ticks' in analysis['qualification']
    assert 'not proof of a verifier update' in analysis['qualification']
    assert analysis['additional_chromium_instrumentation_required']
    report['diagnostics']['netlog'] = summary
    assert lifecycle.assess(report) == assessment
    assert assessment[0] == ['HTTP2 reuse / GOAWAY reconnect sequence failed']


@pytest.mark.parametrize('error', [0, -3, -100, -716])
def test_final_close_error_never_becomes_a_certificate_update_cause(error):
    value, report, responses = certificate_update_fixture()
    names = value['constants']['logEventTypes']
    close = next(e for e in value['events'] if e['type'] == names['HTTP2_SESSION_CLOSE'])
    close['params']['net_error'] = error
    summary = lifecycle.summarize_netlog(lifecycle.project_netlog(value), responses, report)
    c1 = summary['close_analysis']['connections'][0]
    assert c1['cause'] == 'not_proven'
    assert c1['close_events'][0]['net_error'] == error
    expected = 'ok' if error == 0 else 'error'
    assert c1['observation'] == expected + '_close_without_observed_peer_goaway'
    c2 = next(c for c in summary['close_analysis']['connections'] if c['connection_id'] == 5)
    assert c2['observation'] == 'peer_goaway_observed' and c2['cause'] == 'not_proven'


@pytest.mark.parametrize('mode', ['absent', 'outside_session', 'unbound', 'missing_close',
                                 'duplicate_close', 'bad_order', 'truncated'])
def test_certificate_close_analysis_does_not_fill_missing_evidence(mode):
    value, report, responses = certificate_update_fixture()
    names = value['constants']['logEventTypes']
    if mode == 'absent':
        value['events'] = [e for e in value['events'] if e['type'] not in
            {names[n] for n in lifecycle.NETLOG_GLOBAL_LIFECYCLE_EVENTS}]
    elif mode == 'outside_session':
        for event in value['events']:
            if event['type'] in {names[n] for n in lifecycle.NETLOG_GLOBAL_LIFECYCLE_EVENTS}:
                event['time'] = '9999'
    elif mode == 'unbound':
        responses.append(deepcopy(responses[0]))
    elif mode == 'missing_close':
        value['events'] = [e for e in value['events'] if not
            (e['source']['id'] == 81 and e['type'] == names['HTTP2_SESSION_CLOSE'])]
    elif mode == 'duplicate_close':
        value['events'].append(deepcopy(value['events'][2]))
    elif mode == 'bad_order':
        value['events'][2]['time'] = '1'
    elif mode == 'truncated':
        value['events'] = [deepcopy(value['events'][-5]) for _ in range(4096)] + value['events']
    before = deepcopy(report), deepcopy(responses)
    summary = lifecycle.summarize_netlog(lifecycle.project_netlog(value), responses, report)
    assert (report, responses) == before
    analysis = summary['close_analysis']
    c1 = analysis['connections'][0]
    assert c1['global_candidate_indexes_during_session'] == [] and c1['cause'] == 'not_proven'
    if mode in ('absent', 'outside_session'):
        assert c1['observation'] == 'ok_close_without_observed_peer_goaway'
    else:
        assert c1['observation'] == 'incomplete_or_ambiguous' and c1['binding_status'] == 'unresolved'
    assert analysis['summary_truncated'] is (mode == 'truncated')


def test_global_candidate_dependency_does_not_bind_certificate_proc_to_owned_context():
    value, report, responses = certificate_update_fixture()
    value['events'][-6]['params']['source_dependency'] = {'type': 2, 'id': 81, 'secret': 'PRIVATE-edge'}
    projected = lifecycle.project_netlog(value)
    summary = lifecycle.summarize_netlog(projected, responses, report)
    event = next(e for e in summary['events'] if e['source']['id'] == -2**31 + 3)
    assert event['scope'] == 'global_candidate' and event['params'] == {'source_dependency': {'type': 2, 'id': 81}}
    assert all(s['type'] in ('SOCKET', 'HTTP2_SESSION') for b in summary['bindings'] for s in b['sources'])
    assert all(c['cause'] == 'not_proven' for c in summary['close_analysis']['connections'])
    assert 'PRIVATE' not in json.dumps(summary)

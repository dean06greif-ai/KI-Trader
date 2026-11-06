"""Integration tests for iteration 21 (session_0626 improvements).
Covers: telegram notify-catalog / notify-config, min-trade config auth,
ai/equity-curve modes + breakdown, ai/playbook paper+coll fields, ai/setup-usage.
"""
import os
import requests
import pytest

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://strategy-tuner-1.preview.emergentagent.com').rstrip('/')
ADMIN_USER = 'Admin'
ADMIN_PASS = 'Dean06Greif!/Admin'


@pytest.fixture(scope='module')
def admin_token():
    r = requests.post(f'{BASE_URL}/api/auth/login', json={'username': ADMIN_USER, 'password': ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, f'login failed {r.status_code} {r.text}'
    tok = r.json().get('token') or r.json().get('access_token')
    assert tok, r.json()
    return tok


@pytest.fixture(scope='module')
def admin_headers(admin_token):
    return {'Authorization': f'Bearer {admin_token}'}


# --- Telegram notify catalog / config ---

def test_notify_catalog_groups_and_keys():
    r = requests.get(f'{BASE_URL}/api/telegram/notify-catalog', timeout=45)
    assert r.status_code == 200, r.text
    data = r.json()
    groups = data.get('groups') or data
    # flatten keys
    all_keys = set()
    group_names = set()
    def collect(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (dict, list)):
                    collect(v)
                elif isinstance(v, str) and k in ('key',):
                    all_keys.add(v)
            if 'key' in obj and isinstance(obj['key'], str):
                all_keys.add(obj['key'])
            if 'name' in obj and isinstance(obj['name'], str):
                group_names.add(obj['name'])
            if 'label' in obj and isinstance(obj['label'], str):
                group_names.add(obj['label'])
        elif isinstance(obj, list):
            for it in obj:
                collect(it)
    collect(data)
    # also raw string search fallback
    raw = r.text
    required_keys = ['signals_ai', 'trade_opened_live', 'trade_opened_paper', 'order_rejected_internal', 'min_trade']
    for k in required_keys:
        assert k in raw, f'missing key {k} in catalog'
    for g in ['Signale', 'Trades', 'Risiko', 'KI', 'Labor']:
        assert g in raw, f'missing group name containing {g}'


def test_notify_config_persist_paper(admin_headers):
    # read
    r = requests.get(f'{BASE_URL}/api/telegram/notify-config', headers=admin_headers, timeout=15)
    assert r.status_code == 200
    orig = r.json()
    # set trade_opened_paper false
    payload = {'trade_opened_paper': False}
    r2 = requests.post(f'{BASE_URL}/api/telegram/notify-config', headers=admin_headers, json=payload, timeout=15)
    assert r2.status_code in (200, 204), r2.text
    r3 = requests.get(f'{BASE_URL}/api/telegram/notify-config', headers=admin_headers, timeout=15)
    cfg = r3.json()
    # find nested trade_opened_paper
    raw = r3.text
    assert '"trade_opened_paper": false' in raw or '"trade_opened_paper":false' in raw, raw[:400]
    # restore true
    requests.post(f'{BASE_URL}/api/telegram/notify-config', headers=admin_headers, json={'trade_opened_paper': True}, timeout=15)


# --- min-trade config ---

def test_min_trade_config_admin(admin_headers):
    r = requests.get(f'{BASE_URL}/api/min-trade/config', headers=admin_headers, timeout=15)
    assert r.status_code == 200, r.text
    resp = r.json()
    cfg = resp.get('config', resp)  # may be nested under 'config'
    for k in ('enabled', 'target_margin_usdt', 'max_risk_pct', 'max_open', 'paper_fallback_if_impossible'):
        assert k in cfg, f'missing {k} in min-trade config: {cfg}'
    # update max_risk_pct roundtrip
    orig_val = cfg['max_risk_pct']
    new_val = 0.05 if float(orig_val) != 0.05 else 0.07
    r2 = requests.post(f'{BASE_URL}/api/min-trade/config', headers=admin_headers,
                      json={**cfg, 'max_risk_pct': new_val}, timeout=15)
    assert r2.status_code in (200, 204), r2.text
    r3resp = requests.get(f'{BASE_URL}/api/min-trade/config', headers=admin_headers, timeout=15).json()
    r3 = r3resp.get('config', r3resp)
    assert abs(float(r3['max_risk_pct']) - new_val) < 1e-9
    # restore
    requests.post(f'{BASE_URL}/api/min-trade/config', headers=admin_headers, json={**cfg, 'max_risk_pct': orig_val}, timeout=15)


def test_min_trade_config_no_auth():
    r = requests.get(f'{BASE_URL}/api/min-trade/config', timeout=15)
    assert r.status_code in (401, 403), f'expected auth required, got {r.status_code}'


# --- ai equity curve modes ---

@pytest.mark.parametrize('mode', ['real', 'paper', 'live', 'collection'])
def test_ai_equity_curve_modes(mode):
    r = requests.get(f'{BASE_URL}/api/ai/equity-curve', params={'mode': mode}, timeout=20)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get('mode') == mode
    assert 'summary' in data
    assert 'points' in data
    assert 'breakdown' in data
    bd = data['breakdown']
    for w in ('real', 'paper', 'collection'):
        assert w in bd, f'breakdown missing {w}: {bd}'


def test_ai_equity_curve_live_sums_real_paper():
    r_live = requests.get(f'{BASE_URL}/api/ai/equity-curve', params={'mode': 'live'}, timeout=20).json()
    r_real = requests.get(f'{BASE_URL}/api/ai/equity-curve', params={'mode': 'real'}, timeout=20).json()
    r_pap = requests.get(f'{BASE_URL}/api/ai/equity-curve', params={'mode': 'paper'}, timeout=20).json()
    def trades(d):
        s = d.get('summary') or {}
        return int(s.get('trades') or s.get('n_trades') or s.get('count') or 0)
    tl, tr, tp = trades(r_live), trades(r_real), trades(r_pap)
    print(f'trades live={tl} real={tr} paper={tp}')
    assert tl == tr + tp, f'live trades {tl} != real {tr} + paper {tp}'


# --- ai playbook world fields ---

def test_ai_playbook_world_fields():
    r = requests.get(f'{BASE_URL}/api/ai/playbook', timeout=20)
    assert r.status_code == 200
    data = r.json()
    # find maturity rows - may be nested at .maturity, .setups.<name>, .rows
    raw = r.text
    for f in ('paper_trades', 'paper_winrate', 'paper_pnl', 'coll_trades', 'coll_winrate', 'coll_pnl'):
        assert f in raw, f'missing playbook field {f}'


# --- ai setup-usage ---

def test_ai_setup_usage_shape():
    r = requests.get(f'{BASE_URL}/api/ai/setup-usage', params={'days': 14}, timeout=20)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get('days') == 14
    rows = data.get('rows')
    assert isinstance(rows, list)
    if rows:
        row = rows[0]
        for k in ('setup', 'decisions', 'signaled', 'low_conf', 'trades_real', 'trades_paper',
                 'trades_collection', 'top_reasons', 'never_chosen', 'custom'):
            assert k in row, f'missing setup-usage row field {k}: {row}'

#!/usr/bin/env python3
"""Only 7 user-selected mutual funds. Strict, dated public fund NAV/distribution data.

The updater never requests stock/ETF quote APIs, never substitutes another share
class, and never labels cached NAV as today's data if a feed fails. Python stdlib.
"""
from __future__ import annotations
import csv
import io
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, date as Date, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / 'data' / 'live.json'
TPE = timezone(timedelta(hours=8))

SOURCES = {
    'overseas_nav': ['https://opendata.tdcc.com.tw/getOD.ashx?id=3-4'],
    'overseas_info': ['https://opendata.tdcc.com.tw/getOD.ashx?id=3-2'],
    'overseas_dividends': ['https://opendata.tdcc.com.tw/getOD.ashx?id=3-5'],
    'local_nav': [
        'https://members.sitca.org.tw/OPF/K0000/files/F/03/nav.csv',
        'https://www.sitca.org.tw/MemberK0000/F/03/nav.csv',
    ],
    'local_dividends': ['https://www.sitca.org.tw/MemberK0000/F/03/160547%E6%8A%95%E4%BF%A1%E6%8A%95%E9%A1%A7%E5%85%AC%E6%9C%83%E5%A2%83%E5%85%A7%E5%9F%BA%E9%87%91%E9%85%8D%E6%81%AF%E8%B3%87%E6%96%99.csv'],
}

FUNDS = {
    'LU0820561818': dict(type='overseas', currency='USD', display='安聯收益成長 AM穩定月收 美元'),
    'LU1883841964': dict(type='overseas', currency='ZAR', display='東方匯理策略收益債券 A 南非幣'),
    'LU2347655073': dict(type='overseas', currency='USD', display='摩根多重收益 A穩定月配 美元對沖'),
    'LU2023250330': dict(type='overseas', currency='USD', display='安聯收益成長 AMg7 月收總收益 美元'),
    'TW000T0759D2': dict(type='local', currency='USD', tax_id='73637204D', display='瀚亞多重收益優化組合 B 美元'),
    'TW000T1811K5': dict(type='local', currency='USD', tax_id='38522113J', display='聯博多元資產收益組合 AI 美元'),
    'LU2859284783': dict(type='overseas', currency='USD', display='聯博美國成長 AP 美元'),
}


def canonical(s):
    return re.sub(r'[\s\-_/（()）\[\]【】－—]+', '', unicodedata.normalize('NFKC', str(s or '')).upper())


def field(row, *keys):
    normalized = {canonical(k): str(v).strip() if v is not None else '' for k, v in row.items() if k is not None}
    for key in keys:
        value = normalized.get(canonical(key), '')
        if value:
            return value
    return ''


def money(s):
    raw = unicodedata.normalize('NFKC', str(s or '')).strip().replace(',', '')
    if not raw or raw in ('-', '--', 'N/A', '無', 'NULL'):
        return None
    try:
        value = float(raw)
        if not 0 <= value <= 100000000 or value != value or value == float('inf'):
            return None
        return round(value, 8)
    except (ValueError, TypeError):
        return None


def date_str(s):
    s = unicodedata.normalize('NFKC', str(s or '')).strip()
    m = re.search(r'(\d{2,4})[./-](\d{1,2})[./-](\d{1,2})', s)
    if m:
        year, month, day = map(int, m.groups())
    else:
        digits = re.sub(r'\D', '', s)
        if len(digits) == 8:
            year, month, day = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
        elif len(digits) == 7:
            year, month, day = int(digits[:3]), int(digits[3:5]), int(digits[5:7])
        else:
            return ''
    if year < 1911:
        year += 1911
    try:
        d = Date(year, month, day)
        if d > datetime.now(TPE).date() + timedelta(days=1) or year < 2000:
            return ''
        return d.isoformat()
    except ValueError:
        return ''


def currency(s):
    n = canonical(s)
    return {'美元':'USD', '美金':'USD', 'USD':'USD', '南非幣':'ZAR',
            '南非蘭特':'ZAR', 'ZAR':'ZAR'}.get(n, n)


def parse_csv(payload):
    if payload.lstrip()[:40].lower().startswith((b'<html', b'<!doctype')):
        raise ValueError('取得 HTML 網頁而非基金 CSV')
    content = None
    for enc in (('utf-16', 'utf-8-sig', 'cp950', 'big5') if payload.startswith((b'\xff\xfe', b'\xfe\xff')) else ('utf-8-sig', 'cp950', 'big5')):
        try:
            content = payload.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if content is None:
        raise ValueError('無法解碼基金 CSV')
    # Public data sources occasionally contain preamble; expect a recognised header.
    text = content.lstrip('\ufeff')
    lines = text.splitlines()
    header_index = next((i for i, line in enumerate(lines[:12]) if '基金' in line and ('名稱' in line or '代碼' in line)), 0)
    content = '\n'.join(lines[header_index:])
    try:
        dialect = csv.Sniffer().sniff(content[:3500], delimiters=',;\t')
        reader = csv.DictReader(io.StringIO(content), dialect=dialect)
    except csv.Error:
        reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames or len(reader.fieldnames) < 3:
        raise ValueError('基金 CSV 欄位不足')
    result = list(reader)
    if not result:
        raise ValueError('基金 CSV 無資料')
    return result


def download(url):
    request = Request(url, headers={
        'User-Agent': 'Mozilla/5.0 FundRadar/2.0 (+public-fund-data)',
        'Accept': 'text/csv, text/plain, application/octet-stream, */*',
    })
    with urlopen(request, timeout=36) as response:
        return parse_csv(response.read(38_000_000))


def load_source(label, urls):
    errors = []
    for url in urls:
        for attempt in range(2):
            try:
                rows = download(url)
                print(f'[{label}] rows={len(rows)}')
                return {'ok': True, 'rows': rows, 'url': url}
            except Exception as exc:
                errors.append(f'{type(exc).__name__}: {str(exc)[:130]}')
                time.sleep(0.5 if attempt == 0 else 0)
    print(f'[{label}] ERROR: {errors[-1] if errors else "no data"}', file=sys.stderr)
    return {'ok': False, 'rows': [], 'error': (errors[-1] if errors else 'no data')}


def latest_overseas_nav(rows):
    best = {}
    code_map = {}
    for row in rows:
        isin = canonical(field(row, 'ISINCODE', 'ISIN CODE', '基金ISIN_CODE', '基金ISIN Code'))
        spec = FUNDS.get(isin)
        if not spec or spec['type'] != 'overseas':
            continue
        value = money(field(row, '基金淨值', '基金淨值(金額)', '淨值'))
        dt = date_str(field(row, '日期', '淨值日期', '淨值日'))
        unit = field(row, '計價幣別', '基金幣別')
        if not value or not dt or (unit and currency(unit) != spec['currency']):
            continue
        if dt > best.get(isin, {}).get('navDate', ''):
            best[isin] = {'nav': value, 'navDate': dt}
        code = canonical(field(row, '基金代碼', '基金代號'))
        if code:
            code_map[code] = isin
    return best, code_map


def extend_overseas_codes(rows, code_map):
    for row in rows:
        isin = canonical(field(row, '基金ISIN_CODE', 'ISINCODE', 'ISIN CODE', '基金ISIN Code'))
        code = canonical(field(row, '基金代碼', '基金代號'))
        if isin in FUNDS and FUNDS[isin]['type'] == 'overseas' and code:
            code_map[code] = isin
    return code_map


def latest_local_nav(rows):
    best = {}
    for row in rows:
        fid = canonical(field(row, '基金統編', '統編'))
        for isin, spec in FUNDS.items():
            if spec['type'] != 'local' or fid != spec['tax_id']:
                continue
            unit = field(row, '幣別', '計價幣別', '基金幣別')
            if not unit or currency(unit) != spec['currency']:
                continue
            dt = date_str(field(row, '日期', '淨值日', '淨值日期'))
            value = money(field(row, '基金淨值', '淨值'))
            if not dt or not value:
                continue
            if dt > best.get(isin, {}).get('navDate', ''):
                best[isin] = {'nav': value, 'navDate': dt}
    return best


def dividend_record(row):
    ex = date_str(field(row, '除息日', '除息日期'))
    pay = date_str(field(row, '配息發放日', '收益分配發放日', '發放日'))
    amount = money(field(row, '每單位分派金額', '每單位配息金額', '每單位分配金額', '單位數'))
    if not ex or amount is None:
        return None
    return {'exDate': ex, 'payDate': pay, 'perUnit': amount}


def overseas_dividends(rows, code_map):
    found = {}
    for row in rows:
        code = canonical(field(row, '基金代碼', '基金代號'))
        fid = code_map.get(code)
        if not fid:
            continue
        spec = FUNDS[fid]
        unit = field(row, '基金幣別', '計價幣別')
        if unit and currency(unit) != spec['currency']:
            continue
        record = dividend_record(row)
        if record:
            found.setdefault(fid, []).append(record)
    return found


def local_dividends(rows):
    found = {}
    for row in rows:
        tax = canonical(field(row, '基金統編'))
        for fid, spec in FUNDS.items():
            if spec['type'] != 'local' or tax != spec['tax_id']:
                continue
            unit = field(row, '配息幣別', '幣別')
            if not unit or currency(unit) != spec['currency']:
                continue
            record = dividend_record(row)
            if record:
                found.setdefault(fid, []).append(record)
    return found


def merge_records(existing, newer):
    merged = {}
    for r in existing:
        if isinstance(r, dict) and re.fullmatch(r'20\d{2}-\d{2}-\d{2}', str(r.get('exDate',''))):
            merged[r['exDate']] = {k:r.get(k) for k in ('exDate','payDate','perUnit')}
    for r in newer:
        old = merged.get(r['exDate'], {})
        merged[r['exDate']] = {**r, 'payDate': r.get('payDate') or old.get('payDate','')}
    return sorted(merged.values(), key=lambda x: x['exDate'], reverse=True)[:60]


def update_data(existing, sources, at=None):
    at = at or datetime.now(TPE).isoformat(timespec='seconds')
    doc = {'schemaVersion': 2, 'checkedAt': at, 'funds': {}, 'sources': {}}
    for name, source in sources.items():
        doc['sources'][name] = {'ok': source.get('ok', False), 'message': source.get('error', ''),
                                'rows': len(source.get('rows', [])), 'url': source.get('url', ''),
                                'columns': list(source.get('rows', [{}])[0].keys())[:12] if source.get('rows') else []}
    nav_o, codes = latest_overseas_nav(sources['overseas_nav']['rows'])
    extend_overseas_codes(sources['overseas_info']['rows'], codes)
    nav_l = latest_local_nav(sources['local_nav']['rows'])
    div_o = overseas_dividends(sources['overseas_dividends']['rows'], codes)
    div_l = local_dividends(sources['local_dividends']['rows'])
    for fid, spec in FUNDS.items():
        old = existing.get('funds',{}).get(fid,{})
        latest_nav = (nav_o if spec['type'] == 'overseas' else nav_l).get(fid)
        new_divs = (div_o if spec['type'] == 'overseas' else div_l).get(fid,[])
        nav = old.get('nav') if isinstance(old.get('nav'), (int,float)) else None
        nav_dt = old.get('navDate','')
        used_new_nav = bool(latest_nav and latest_nav['navDate'] >= nav_dt)
        if used_new_nav:
            nav, nav_dt = latest_nav['nav'], latest_nav['navDate']
        nav_label = 'overseas_nav' if spec['type'] == 'overseas' else 'local_nav'
        dist_label = 'overseas_dividends' if spec['type'] == 'overseas' else 'local_dividends'
        nav_message = ('已找到這檔基金的官方淨值' if used_new_nav
                       else ('官方來源下載失敗' if not sources[nav_label]['ok']
                             else '官方淨值檔未比對到相同的 ISIN／基金統編與幣別'))
        div_message = ('已找到配息紀錄' if new_divs
                       else ('官方配息來源下載失敗' if not sources[dist_label]['ok']
                             else '配息檔未比對到這檔基金，或目前未公告'))
        entry = {
            'nav': nav, 'navDate': nav_dt,
            'navMessage': nav_message, 'dividendMessage': div_message,
            'navStatus': 'found' if used_new_nav else ('cached' if nav is not None else 'unavailable'),
            'dividends': merge_records(old.get('dividends', []), new_divs),
            'dividendStatus': 'found' if new_divs else ('cached' if old.get('dividends') else 'unavailable'),
        }
        doc['funds'][fid] = entry
        print(f'[{fid}] NAV={entry["navStatus"]} ({entry["navDate"] or "—"}); 配息={len(entry["dividends"])} 筆; {nav_message}')
    count = sum(1 for f in doc['funds'].values() if f['navStatus'] == 'found')
    print(f'Fund NAV matches: {count}/7; distributions: {sum(bool(f["dividends"]) for f in doc["funds"].values())}/7')
    return doc


def main():
    prior = json.loads(LIVE.read_text(encoding='utf-8')) if LIVE.exists() else {'funds':{}}
    downloaded = {name: load_source(name, urls) for name,urls in SOURCES.items()}
    doc = update_data(prior, downloaded)
    LIVE.parent.mkdir(parents=True, exist_ok=True)
    LIVE.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    # Feed failures must not be presented as successful NAV checks; we still publish
    # the status metadata so the website can display an accurate warning.
    print('Updated data/live.json')

if __name__ == '__main__':
    main()

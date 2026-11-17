#!/usr/bin/env python3
"""Only 7 user-selected mutual funds. Strict, dated public fund NAV/distribution data.

The updater never requests stock/ETF quote APIs, never substitutes another share
class, and never labels cached NAV as today's data if a feed fails. Python stdlib.
"""
from __future__ import annotations
import csv
import io
from html.parser import HTMLParser
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


# Backup: publicly visible, share-class-specific NAV pages. This is a THIRD-PARTY
# reference, not an official subscription/redemption NAV source. A backup quote
# can only replace a same-class quote when its date is strictly newer.
# Stop using a page automatically if its heading/currency/format changes.
NAV_BACKUP_PAGES = {
    'LU0820561818': ('https://m.moneydj.com/b1.aspx?a=TLZ64', '安聯收益成長基金-AM穩定月收類股(美元)'),
    'LU2023250330': ('https://m.moneydj.com/b1.aspx?a=TLZF9', '安聯收益成長基金-AMg7月收總收益類股(美元)'),
    'LU2347655073': ('https://m.moneydj.com/b1.aspx?a=JFZN3', '摩根投資基金-多重收益基金A股(美元對沖)(穩定月配)'),
    'TW000T0759D2': ('https://m.moneydj.com/a1.aspx?a=ACCP138', '瀚亞多重收益優化組合基金B配息(美元)'),
    'TW000T1811K5': ('https://m.moneydj.com/a1.aspx?a=ACTI71', '聯博多元資產收益組合基金AI配息(美元)'),
    'LU2859284783': ('https://m.moneydj.com/b1.aspx?a=ALBT8', '聯博-美國成長基金AP(總報酬月配)級別美元'),
    'LU1883841964': ('https://m.moneydj.com/b1.aspx?a=PIZC5', '東方匯理基金策略收益債券A南非幣避險(穩定月配息)'),
}

class _FundPageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.headings = []
        self.in_heading = False
        self.heading_parts = []
        self.ignore_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.ignore_depth += 1
        if tag == 'h1':
            self.in_heading = True
            self.heading_parts = []

    def handle_endtag(self, tag):
        if tag == 'h1':
            self.headings.append(''.join(self.heading_parts).strip())
            self.in_heading = False
        if tag in ('script', 'style') and self.ignore_depth > 0:
            self.ignore_depth -= 1

    def handle_data(self, data):
        if self.ignore_depth:
            return
        self.parts.append(data)
        if self.in_heading:
            self.heading_parts.append(data)


def parse_backup_nav_html(payload, expected_heading, expected_currency):
    """Exact visible fund/share class, specified currency, and explicit NAV date required.

    Do not use performance, dividend, graph, or previous-date numbers. No network
    dependency: this parser can be tested against representative HTML fixtures.
    """
    text = None
    for encoding in ('utf-8-sig', 'big5', 'cp950'):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            pass
    if text is None:
        raise ValueError('基金備援 HTML 文字編碼無法判讀')
    page = _FundPageText()
    page.feed(text)
    actual_titles = [canonical(h) for h in page.headings]
    if not actual_titles or not any(h.startswith(canonical(expected_heading)) for h in actual_titles):
        raise ValueError('備援頁面基金名稱/級別不符')
    visible = re.sub(r'\s+', ' ', ' '.join(page.parts))
    currency_word = '美元' if expected_currency == 'USD' else '南非幣'
    pat = re.compile(r'最新值\s*([0-9,]+(?:\.[0-9]+)?)\s*' + currency_word +
                     r'\s*[（(]\s*(20\d{2}[./-]\d{1,2}[./-]\d{1,2})\s*[）)]')
    m = pat.search(visible)
    if not m:
        raise ValueError('備援頁面找不到明確的最新淨值、幣別及日期')
    nav, dt = money(m.group(1)), date_str(m.group(2))
    if nav is None or nav <= 0 or not dt:
        raise ValueError('備援頁面淨值或日期異常')
    return {'nav': nav, 'navDate': dt, 'navSource': 'MoneyDJ 基金專頁（備援，非基金公司官方）'}


def download_backup_nav():
    found, results = {}, {}
    for fid, (url, expected) in NAV_BACKUP_PAGES.items():
        try:
            req = Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; FundRadar/3.0; personal-watchlist)',
                'Accept': 'text/html,application/xhtml+xml',
            })
            with urlopen(req, timeout=20) as r:
                payload = r.read(1_200_000)
            item = parse_backup_nav_html(payload, expected, FUNDS[fid]['currency'])
            item['navSourceUrl'] = url
            found[fid] = item
            results[fid] = {'ok': True, 'navDate': item['navDate'], 'url': url}
            print(f'[nav_backup] {fid}: {item["navDate"]} {item["nav"]} ({url})')
        except Exception as e:
            msg = f'{type(e).__name__}: {str(e)[:120]}'
            results[fid] = {'ok': False, 'error': msg, 'url': url}
            print(f'[nav_backup] {fid}: unavailable ({msg})', file=sys.stderr)
    return found, results


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
    for enc in ('utf-8-sig', 'cp950', 'big5'):
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


def update_data(existing, sources, at=None, backup_nav=None, backup_results=None):
    at = at or datetime.now(TPE).isoformat(timespec='seconds')
    backup_nav = backup_nav or {}
    backup_results = backup_results or {}
    doc = {'schemaVersion': 2, 'checkedAt': at, 'funds': {}, 'sources': {}}
    for name, source in sources.items():
        doc['sources'][name] = {
            'ok': source.get('ok', False),
            'message': source.get('error', ''),
            'rows': len(source.get('rows', [])),
        }
    backup_ok = sum(r.get('ok', False) for r in backup_results.values())
    doc['sources']['nav_backup'] = {
        'ok': backup_ok > 0, 'rows': backup_ok,
        'message': f'基金備援成功 {backup_ok}/{len(NAV_BACKUP_PAGES)} 檔；依各基金淨值日期選用',
    }
    doc['navBackupDetails'] = backup_results
    nav_o, codes = latest_overseas_nav(sources['overseas_nav']['rows'])
    extend_overseas_codes(sources['overseas_info']['rows'], codes)
    nav_l = latest_local_nav(sources['local_nav']['rows'])
    div_o = overseas_dividends(sources['overseas_dividends']['rows'], codes)
    div_l = local_dividends(sources['local_dividends']['rows'])
    for fid, spec in FUNDS.items():
        old = existing.get('funds', {}).get(fid, {})
        standard = (nav_o if spec['type'] == 'overseas' else nav_l).get(fid)
        prior_nav = old.get('nav') if isinstance(old.get('nav'), (int, float)) else None
        prior_date = old.get('navDate') or ''
        candidate = None
        if standard:
            candidate = {
                **standard,
                'navSource': '集保境外基金公開資料' if spec['type'] == 'overseas' else '投信投顧公會境內基金公開資料',
            }
        secondary = backup_nav.get(fid)
        if secondary and (not candidate or secondary['navDate'] > candidate['navDate']):
            candidate = secondary
        # Never go backwards even if all upstream sources are late.
        accepted = bool(candidate and candidate['navDate'] >= prior_date)
        nav = candidate['nav'] if accepted else prior_nav
        nav_dt = candidate['navDate'] if accepted else prior_date
        nav_source = (candidate.get('navSource') if accepted else old.get('navSource')) or '歷史快照（原來源未記錄）'
        new_divs = (div_o if spec['type'] == 'overseas' else div_l).get(fid, [])
        parsed_nav_date = date_str(nav_dt)
        delayed_days = max(0, (datetime.now(TPE).date() - Date.fromisoformat(parsed_nav_date)).days) if parsed_nav_date else None
        entry = {
            'nav': nav,
            'navDate': nav_dt,
            'navSource': nav_source,
            'navSourceUrl': candidate.get('navSourceUrl', '') if accepted else old.get('navSourceUrl', ''),
            'navStatus': 'found' if accepted else ('cached' if nav is not None else 'unavailable'),
            'navDelayedDays': delayed_days,
            'navMessage': f'淨值已落後 {delayed_days} 天，請核對基金公司' if delayed_days is not None and delayed_days >= 4 else '',
            'dividends': merge_records(old.get('dividends', []), new_divs),
            'dividendStatus': 'found' if new_divs else ('cached' if old.get('dividends') else 'unavailable'),
        }
        doc['funds'][fid] = entry
        print(f'[NAV] {fid}: {nav_dt or "no date"} {nav if nav is not None else "no NAV"}; '
              f'source={nav_source}; status={entry["navStatus"]}; delayedDays={delayed_days}')
    print(f'基金淨值日期 >= 3 日前: {sum(1 for f in doc["funds"].values() if f.get("navDelayedDays") is not None and f["navDelayedDays"] <= 3)}/7')
    return doc


def main():
    prior = json.loads(LIVE.read_text(encoding='utf-8')) if LIVE.exists() else {'funds':{}}
    downloaded = {name: load_source(name, urls) for name,urls in SOURCES.items()}
    backup_nav, backup_results = download_backup_nav()
    doc = update_data(prior, downloaded, backup_nav=backup_nav, backup_results=backup_results)
    LIVE.parent.mkdir(parents=True, exist_ok=True)
    LIVE.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    # Feed failures must not be presented as successful NAV checks; we still publish
    # the status metadata so the website can display an accurate warning.
    print('Updated data/live.json')

if __name__ == '__main__':
    main()

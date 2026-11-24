#!/usr/bin/env python3
"""Only 11 user-selected mutual funds. Strict, dated public fund NAV/distribution data.

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
    'LU1075101847': dict(type='overseas', currency='ZAR', display='高盛基金III 新興市場債券 X股南非幣對沖月配息'),
    'LU2347655073': dict(type='overseas', currency='USD', display='摩根多重收益 A穩定月配 美元對沖'),
    'LU2023250330': dict(type='overseas', currency='USD', display='安聯收益成長 AMg7 月收總收益 美元'),
    'TW000T0759D2': dict(type='local', currency='USD', tax_id='73637204D', display='瀚亞多重收益優化組合 B 美元'),
    'TW000T1811K5': dict(type='local', currency='USD', tax_id='38522113J', display='聯博多元資產收益組合 AI 美元'),
    'LU2859284783': dict(type='overseas', currency='USD', display='聯博美國成長 AP 美元'),
    'LU1900986214': dict(type='overseas', currency='USD', display='施羅德環球收益成長 A月配固定2 美元'),
    'LU3047255321': dict(type='overseas', currency='USD', display='富達全球動能多元 A股C月配息 美元'),
    'LU2110300469': dict(type='overseas', currency='USD', display='景順環球高評級企業債券 E 穩定月配息 美元'),
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
    'LU1900986214': ('https://m.moneydj.com/b1.aspx?a=PYZW3', '施羅德環球基金系列-環球收益成長(美元)A-月配固定2'),
    'LU3047255321': ('https://m.moneydj.com/b1.aspx?a=FTZA67', '富達基金-全球動能多元基金(A股C月配息美元)'),
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


JPM_ISIN = 'LU2347655073'
JPM_BANK_PAGES = (
    ('https://tbb.moneydj.com/mobile/b1.aspx?a=JFZN3-01G1', '臺灣企銀基金資訊（嘉實資料）', 'mobile'),
    ('https://newfund.tw.dbs.com/mobile/b1.aspx?a=JFZN3', '星展銀行基金資訊（嘉實資料）', 'mobile'),
    ('https://fund.hncb.com.tw/w/wb/wb02.djhtm?a=JFZN3-01I3', '華南銀行基金歷史淨值（嘉實資料）', 'history'),
)


def parse_jpm_bank_nav(payload, mode):
    """Read only the exact A USD-hedged stable monthly class.

    Bank mobile pages use 最新淨值 rather than 最新值; bank history pages
    expose a date/NAV table instead. Never parse a graph or distribution table.
    """
    page = _FundPageText()
    for encoding in ('utf-8-sig', 'cp950', 'big5'):
        try:
            raw = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError('銀行基金頁文字編碼不符')
    page.feed(raw)
    visible = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', ' '.join(page.parts))).strip()
    # Remove whitespace only in the known title, not in the quote or date table.
    title = r'摩根投資基金\s*[-－]?\s*多重收益基金\s*A股\s*\(美元對沖\)\s*\(穩定月配\)'
    if mode == 'mobile':
        pat = title + r'(?:\([^()]{1,180}\))?\s*(?:觀察\s*)?最新淨值\s*[:：]?\s*([0-9,]+(?:\.[0-9]+)?)\s*美元\s*\(\s*(20\d{2}[/.-]\d{1,2}[/.-]\d{1,2})\s*\)'
        m = re.search(pat, visible)
        if not m:
            raise ValueError('銀行頁未找到相符 A 股美元對沖穩定月配的即日淨值')
        nav, dt = money(m.group(1)), date_str(m.group(2))
    elif mode == 'history':
        pat = title + r'\s*[-－]?\s*近30日淨值\s*日期\s*淨值\s*漲跌幅\s*\(%\)\s*(20\d{2}[/.-]\d{1,2}[/.-]\d{1,2})\s+([0-9,]+(?:\.[0-9]+)?)\s+[-+−]?[0-9.,]+(?:\s*%)?'
        m = re.search(pat, visible)
        if not m:
            raise ValueError('銀行頁未找到相符 A 股美元對沖穩定月配的歷史淨值表')
        dt, nav = date_str(m.group(1)), money(m.group(2))
    else:
        raise ValueError('未知銀行基金頁格式')
    if not nav or not dt or dt > datetime.now(TPE).date().isoformat():
        raise ValueError('摩根淨值或日期異常')
    return {'nav': nav, 'navDate': dt}


def _get_nav_page(url):
    req = Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.5',
    })
    with urlopen(req, timeout=20) as r:
        return r.read(1_200_000)


def download_jpm_nav():
    primary, expected = NAV_BACKUP_PAGES[JPM_ISIN]
    pages = [(primary, 'MoneyDJ 基金專頁（備援，非基金公司官方）', 'primary'), *JPM_BANK_PAGES]
    quotes, checks = [], []
    for url, label, mode in pages:
        try:
            payload = _get_nav_page(url)
            item = (parse_backup_nav_html(payload, expected, 'USD') if mode == 'primary'
                    else parse_jpm_bank_nav(payload, mode))
            if item['navDate'] > datetime.now(TPE).date().isoformat():
                raise ValueError('淨值日期晚於今日')
            item.update(navSource=label, navSourceUrl=url)
            quotes.append(item)
            checks.append(dict(ok=True, url=url, nav=item['nav'], navDate=item['navDate']))
            print(f'[JPM NAV] {item["navDate"]} {item["nav"]} {url}')
        except Exception as e:
            msg = f'{type(e).__name__}: {str(e)[:150]}'
            checks.append(dict(ok=False, url=url, error=msg))
            print(f'[JPM NAV] unavailable: {url} {msg}', file=sys.stderr)
    if not quotes:
        return None, dict(ok=False, checks=checks, error='摩根指定級別所有備援頁均未取得淨值')
    latest = max(x['navDate'] for x in quotes)
    candidates = [x for x in quotes if x['navDate'] == latest]
    values = {round(x['nav'], 4) for x in candidates}
    if len(values) > 1:
        return None, dict(ok=False, checks=checks, error='同日備援淨值不一致，暫不採用，請核對摩根官方資訊')
    item = candidates[0]
    return item, dict(ok=True, navDate=latest, url=item['navSourceUrl'], checks=checks)


def download_backup_nav():
    found, results = {}, {}
    for fid, (url, expected) in NAV_BACKUP_PAGES.items():
        if fid == JPM_ISIN:
            item, detail = download_jpm_nav()
            results[fid] = detail
            if item:
                found[fid] = item
                print(f'[nav_backup] {fid}: {item["navDate"]} {item["nav"]} ({item["navSourceUrl"]})')
            continue
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


# 用可再利用的基金開放資料累積同級別「歷史淨值」，供除息率依前一淨值日計算。
# 來源：集保境外基金淨值（近7日）、投信投顧公會境內基金每日淨值。
# 不會倒推淨值，也不以「最新淨值」冒充除息前基準淨值。
HISTORY_LIMIT = 400
BASIS_MAX_CALENDAR_DAYS = 7


def _valid_ymd(value):
    if not isinstance(value, str) or not re.fullmatch(r'20\d{2}-\d{2}-\d{2}', value):
        return False
    try:
        return Date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def authorized_history(source_rows, is_overseas):
    collected = {}
    tax_map = {v['tax_id']:k for k,v in FUNDS.items() if v['type']=='local'}
    for row in source_rows:
        if is_overseas:
            fid = canonical(field(row,'ISINCODE','ISIN CODE','基金ISIN_CODE','基金ISIN Code'))
            spec = FUNDS.get(fid)
            if not spec or spec['type']!='overseas': continue
            unit = field(row,'計價幣別','基金幣別')
            if unit and currency(unit)!=spec['currency']: continue
            nav = money(field(row,'基金淨值(金額)','基金淨值','淨值'))
        else:
            fid = tax_map.get(canonical(field(row,'基金統編','統編')))
            if not fid: continue
            unit = field(row,'幣別','計價幣別','基金幣別')
            if not unit or currency(unit)!=FUNDS[fid]['currency']: continue
            nav = money(field(row,'基金淨值','淨值'))
        day = date_str(field(row,'日期','淨值日','淨值日期'))
        if nav is not None and nav>0 and _valid_ymd(day):
            collected.setdefault(fid,{})[day]={'navDate':day,'nav':nav,'dataOrigin':'open-data'}
    return collected


def merge_authorized_history(old, incoming, source_type=None):
    found = {}
    for r in old.get('navHistory',[]) if isinstance(old.get('navHistory'),list) else []:
        if not isinstance(r,dict) or r.get('dataOrigin')!='open-data': continue
        date=r.get('navDate');nav=r.get('nav')
        if _valid_ymd(date) and isinstance(nav,(int,float)) and 0<nav<1e8:
            found[date]={'navDate':date,'nav':nav,'dataOrigin':'open-data'}
    # 只可從明確的集保、公會淨值補入快照，絕不把 MoneyDJ 備援冒充開放資料。
    label=str(old.get('navSource',''))
    if ('集保' in label or '投信投顧公會' in label) and _valid_ymd(old.get('navDate')):
        val=old.get('nav')
        if isinstance(val,(int,float)) and 0<val<1e8:
            day=old['navDate'];found.setdefault(day,{'navDate':day,'nav':val,'dataOrigin':'open-data'})
    for date, r in incoming.items():
        if _valid_ymd(date) and isinstance(r.get('nav'),(int,float)) and 0<r['nav']<1e8:
            found[date]={'navDate':date,'nav':r['nav'],'dataOrigin':'open-data'}
    return sorted(found.values(),key=lambda r:r['navDate'],reverse=True)[:HISTORY_LIMIT]


def _consecutive_valuation_day(prior_day, ex_day):
    """保守確認兩者之間沒有未取得的平日淨值；連假缺口則暫不計算。"""
    start=Date.fromisoformat(prior_day)
    end=Date.fromisoformat(ex_day)
    gap=(end-start).days
    return 1<=gap<=BASIS_MAX_CALENDAR_DAYS and all(
        (start+timedelta(days=d)).weekday()>=5 for d in range(1,gap)
    )


def attach_authorized_basis(entry):
    history=entry.get('navHistory',[])
    for r in entry.get('dividends',[]):
        ex=r.get('exDate','')
        if not _valid_ymd(ex) or not isinstance(r.get('perUnit'),(int,float)):continue
        nearest=next((h for h in history if h['navDate']<ex and _consecutive_valuation_day(h['navDate'],ex)),None)
        if nearest:
            r.update(preExNav=nearest['nav'],preExNavDate=nearest['navDate'],
                preExNavOrigin='open-data',preExNavSource='政府開放資料：同級別除息前一淨值日',
                preExNavSourceUrl=('https://data.gov.tw/dataset/11641' if entry['kind']=='overseas' else 'https://data.gov.tw/dataset/11109'))
    entry.pop('kind',None)
    return entry


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
    hist_o = authorized_history(sources['overseas_nav']['rows'],True)
    hist_l = authorized_history(sources['local_nav']['rows'],False)
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
            'navHistory':merge_authorized_history(old,(hist_o if spec['type']=='overseas' else hist_l).get(fid,{})),
            'dividendStatus': 'found' if new_divs else ('cached' if old.get('dividends') else 'unavailable'),
        }
        if fid == JPM_ISIN:
            warnings = []
            if standard and secondary and standard['navDate'] == secondary['navDate'] and abs(standard['nav']-secondary['nav']) > 0.00005:
                warnings.append(f"摩根同日淨值不一致：集保 {standard['nav']}、備援 {secondary['nav']}；暫依集保，請核對基金公司")
            if not secondary and backup_results.get(fid, {}).get('error'):
                warnings.append(backup_results[fid]['error'])
            if warnings:
                entry['navMessage'] = '；'.join(filter(None, [entry['navMessage'], *warnings]))
                print('[JPM NAV warning]', entry['navMessage'])
        doc['funds'][fid] = entry
        print(f'[NAV] {fid}: {nav_dt or "no date"} {nav if nav is not None else "no NAV"}; '
              f'source={nav_source}; status={entry["navStatus"]}; delayedDays={delayed_days}')
    print(f'淨值更新延遲 3 日內: {sum(1 for f in doc["funds"].values() if f.get("navDelayedDays") is not None and f["navDelayedDays"] <= 3)}/{len(FUNDS)}')
    return doc


# Distribution reference: exact same share class and currency, never latest NAV.
from concurrent.futures import ThreadPoolExecutor

class _YieldTables(_FundPageText):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None
    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        if tag == 'tr': self.row = []
        elif tag in ('td','th') and self.row is not None: self.cell = []
    def handle_data(self, data):
        super().handle_data(data)
        if self.cell is not None and not self.ignore_depth: self.cell.append(data)
    def handle_endtag(self, tag):
        super().handle_endtag(tag)
        if tag in ('td','th') and self.cell is not None:
            if self.row is not None: self.row.append(''.join(self.cell).strip())
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None

def yield_page(payload, expected):
    for enc in ('utf-8-sig','cp950','big5'):
        try: text = payload.decode(enc); break
        except UnicodeDecodeError: continue
    else: raise ValueError('無法解碼配息資料')
    page = _YieldTables(); page.feed(text)
    title = canonical(expected)
    if not any(canonical(h).startswith(title) for h in page.headings):
        raise ValueError('基金級別不符')
    return page

def parse_distribution_rates(payload, expected, ccy):
    page=yield_page(payload,expected); found={}; active=False
    for row in page.rows:
        h=[canonical(x).replace('%','') for x in row]
        if h == ['除息日','幣別','息值','年化配息率']:
            active=True; continue
        if not active or len(row)!=4: continue
        ex=date_str(row[0]); amount=money(row[2]); rate=money(row[3].rstrip('%').strip())
        if ex and currency(row[1])==ccy and amount is not None and rate is not None and rate <= 1000:
            found[ex]={'perUnit':amount,'reportedAnnualRate':rate}
    if not found: raise ValueError('找不到指定幣別的年化配息率表')
    return found

def parse_recent_nav(payload, expected, ccy):
    page=yield_page(payload,expected)
    latest=parse_backup_nav_html(payload,expected,ccy)['navDate']
    anchor=Date.fromisoformat(latest); found={}; active=False
    for row in page.rows:
        if [canonical(x) for x in row]==['日期','淨值']:
            active=True; continue
        if not active or len(row)!=2: continue
        m=re.fullmatch(r'(\d{1,2})/(\d{1,2})',row[0].strip()); v=money(row[1])
        if not m or not v: continue
        month,day=map(int,m.groups())
        try:
            dt=Date(anchor.year,month,day)
            if dt>anchor: dt=Date(anchor.year-1,month,day)
        except ValueError: continue
        if 0 <= (anchor-dt).days <= 60: found[dt.isoformat()]=v
    if not found: raise ValueError('找不到近20日淨值表')
    return found

def download_yield_data():
    def one(item):
        fid,(nav_url,heading)=item; ccy=FUNDS[fid]['currency']
        div_url=nav_url.replace('/b1.aspx','/B5.aspx').replace('/a1.aspx','/a5.aspx')
        result={'navUrl':nav_url,'rateUrl':div_url,'navs':{},'rates':{},'errors':[]}
        for key,url,parser in [('navs',nav_url,parse_recent_nav),('rates',div_url,parse_distribution_rates)]:
            try: result[key]=parser(_get_nav_page(url),heading,ccy)
            except Exception as e: result['errors'].append(f'{key}: {type(e).__name__}: {e}')
        print(f'[配息率] {fid}: 歷史淨值 {len(result["navs"])} 筆；公開年化率 {len(result["rates"])} 筆；{result["errors"]}')
        return fid,result
    with ThreadPoolExecutor(max_workers=4) as pool: return dict(pool.map(one,NAV_BACKUP_PAGES.items()))

# 固定日期的一次性歷史查核資料，非自動爬取、非未來配息率預測。
# 高盛基金III 新興市場債券 X股南非幣對沖月配（ISIN LU1075101847）：
# - 安泰銀行 2026-11-03 除息、每單位 15.70 南非幣、2026-11-06 發放
# - 華南銀行 2026-11-02 歷史淨值 733.7100 南非幣
# 當期 15.7 / 733.71 *100 = 2.13981%；年化 15.7 / 733.71 *1200 = 25.67772%。
# 只用於相同 ISIN、幣別及除息日的這一期，12 月及未來不繼承。
VERIFIED_GOLDMAN_NOV_2026 = {
    'isin': 'LU1075101847',
    'exDate': '2026-11-03',
    'payDate': '2026-11-06',
    'perUnit': 15.7,
    'preExNavDate': '2026-11-02',
    'preExNav': 733.71,
    'preExNavSource': '華南銀行（同級別歷史淨值，人工核對）',
    'preExNavSourceUrl': 'https://fund.hncb.com.tw/w/wb/wb02.djhtm?a=ANZH1-4745',
    'dividendSource': '安泰銀行（同級別配息紀錄，人工核對）',
    'dividendSourceUrl': 'https://fundrwd.entiebank.com.tw/w/wb/wb05.djhtm?a=ANZH1-3346',
}


def add_verified_goldman_november(fid, entry):
    """只補 2026/11/03 已核實的 X-ZAR 級別。配息值衝突時不覆蓋官方來源。"""
    check=VERIFIED_GOLDMAN_NOV_2026
    if fid != check['isin'] or FUNDS[fid]['currency']!='ZAR':
        return entry
    history=entry.get('dividends',[])
    record=next((r for r in history if r.get('exDate')==check['exDate']),None)
    if record is not None:
        try: same_amount=abs(float(record['perUnit'])-check['perUnit'])<0.0000001
        except (TypeError,ValueError,KeyError): same_amount=False
        if not same_amount:
            entry['dividendMessage'] = ('2026/11/03 配息值與銀行查核資料不一致；'
                                        '未補入該期歷史基準淨值，請人工核對')
            return entry
    else:
        history=merge_records(history,[{
            'exDate':check['exDate'],'payDate':check['payDate'],
            'perUnit':check['perUnit'],'dividendSource':check['dividendSource'],
            'dividendSourceUrl':check['dividendSourceUrl'],
        }])
        entry['dividends']=history
        entry['dividendStatus']='found'
        record=next(r for r in history if r['exDate']==check['exDate'])
    if not record.get('payDate'): record['payDate']=check['payDate']
    record.setdefault('dividendSource',check['dividendSource'])
    record.setdefault('dividendSourceUrl',check['dividendSourceUrl'])
    # 開放資料中已核實的除息前淨值優先；否則只補這個日期的人工查核值。
    if record.get('preExNavOrigin')!='open-data':
        record.update(preExNav=check['preExNav'],preExNavDate=check['preExNavDate'],
                      preExNavSource=check['preExNavSource'],
                      preExNavSourceUrl=check['preExNavSourceUrl'],
                      preExNavOrigin='bank-verified-one-off')
    return entry


def enrich_distribution_rates(doc, prior, inputs):
    for fid, entry in doc['funds'].items():
        data=inputs.get(fid,{}); navs=data.get('navs',{}); reported=data.get('rates',{})
        # The two newly added overseas share classes may not yet appear in the
        # TDCC dividend-code crosswalk. Use exact-class MoneyDJ distribution
        # records as clearly labeled reference data only for missing periods.
        if fid in ('LU1900986214', 'LU3047255321') and reported:
            present = {r['exDate'] for r in entry.get('dividends', [])}
            supplemental = [
                {'exDate': ex, 'payDate': '', 'perUnit': row['perUnit'],
                 'dividendSource': 'MoneyDJ 同級別公開配息紀錄（參考）'}
                for ex, row in reported.items() if ex not in present
            ]
            if supplemental:
                entry['dividends'] = merge_records(entry.get('dividends', []), supplemental)
                entry['dividendStatus'] = 'found'
                entry['dividendMessage'] = '部分配息資料來自 MoneyDJ 同級別公開紀錄（參考）；發放日未提供時不推定'
        prev={r.get('exDate'):r for r in prior.get('funds',{}).get(fid,{}).get('dividends',[])}
        ordered=sorted(navs)
        for r in entry.get('dividends',[]):
            ex=r['exDate']; amount=r.get('perUnit'); old=prev.get(ex,{})
            if amount is None: continue
            # Carry only same-period, same-amount reference metadata.
            keys=('preExNav','preExNavDate','preExNavSource','preExNavSourceUrl','reportedAnnualRate','reportedAnnualSource','reportedAnnualSourceUrl')
            if old.get('perUnit')==amount:
                r.update({k:old[k] for k in keys if k in old})
            row=reported.get(ex)
            if row and abs(row['perUnit']-amount)<0.00000001:
                r.update(reportedAnnualRate=row['reportedAnnualRate'],reportedAnnualSource='MoneyDJ 公開年化配息率（參考）',reportedAnnualSourceUrl=data['rateUrl'])
            # Select previous valuation day from the SAME source's daily series only
            # when both the ex-date and the immediately preceding record are present.
            if ex in navs:
                pos=ordered.index(ex)
                if pos:
                    before=ordered[pos-1]; value=navs[before]
                    gap=(Date.fromisoformat(ex)-Date.fromisoformat(before)).days
                    rate=amount/value*1200 if value>0 else None
                    # If published annual rate disagrees, leave the published rate
                    # as a separately attributed reference; do not claim a basis NAV.
                    if 1<=gap<=7 and rate is not None and (not row or abs(row['perUnit']-amount)>=1e-8 or abs(rate-row['reportedAnnualRate'])<=0.011):
                        r.update(preExNav=value,preExNavDate=before,preExNavSource='MoneyDJ 歷史淨值（前一淨值日）',preExNavSourceUrl=data['navUrl'])
            # No inference of NAV by reversing a rounded published yield.
        # 若政府開放資料已有本期的除息前淨值，優先取代第三方基準。
        entry['kind']=FUNDS[fid]['type']
        attach_authorized_basis(entry)
        add_verified_goldman_november(fid,entry)
        print(f'[配息率] {fid}: 基準淨值 {sum("preExNav" in r for r in entry.get("dividends",[]))} 筆；公開年化率 {sum("reportedAnnualRate" in r for r in entry.get("dividends",[]))} 筆')
    doc['sources']['dividend_yield']={'ok':any(x.get('navs') or x.get('rates') for x in inputs.values()),'rows':sum(len(x.get('rates',{})) for x in inputs.values()),'message':'同級別歷史淨值與公開年化配息率，缺資料不以最新淨值代替'}
    return doc


def main():
    print('[NAV updater] ELEVEN-FUNDS-20261124 + GOLDMAN-NOV-DIVIDEND-VERIFIED-20261123')
    prior = json.loads(LIVE.read_text(encoding='utf-8')) if LIVE.exists() else {'funds':{}}
    downloaded = {name: load_source(name, urls) for name,urls in SOURCES.items()}
    backup_nav, backup_results = download_backup_nav()
    doc = update_data(prior, downloaded, backup_nav=backup_nav, backup_results=backup_results)
    enrich_distribution_rates(doc, prior, download_yield_data())
    LIVE.parent.mkdir(parents=True, exist_ok=True)
    LIVE.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    # Feed failures must not be presented as successful NAV checks; we still publish
    # the status metadata so the website can display an accurate warning.
    print('Updated data/live.json')

if __name__ == '__main__':
    main()
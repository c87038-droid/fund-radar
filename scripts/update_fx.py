#!/usr/bin/env python3
"""Bank of Taiwan spot (即期) USD/ZAR buy/sell quote updater.

Output: data/fx.json. No hardcoded market prices, no stock APIs.
When unavailable, previously confirmed rates remain tagged cached and errors
are published for display; missing prices are never fabricated.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / 'data' / 'fx.json'
TW = timezone(timedelta(hours=8))
CODES = ('USD', 'ZAR')
SCRIPT_VERSION = "FX-TEXT-V2-20261118"
RATE_PAGE = 'https://rate.bot.com.tw/xrt?Lang=zh-TW'
RATE_TEXT = 'https://rate.bot.com.tw/xrt/fltxt/0/day'
# First use the official plain-text board: this endpoint has a stable,
# labeled Buying / Selling layout and avoids fragile HTML table selectors.
SOURCES = (
    ('臺灣銀行官方文字牌告', RATE_TEXT, 'text'),
    ('臺灣銀行即期牌告網頁', RATE_PAGE, 'html'),
    ('臺灣銀行 CSV', 'https://rate.bot.com.tw/xrt/flcsv/0/day', 'csv'),
)



class TableReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None
        self.cell_attrs = None
        self.row_details = None
        self.detailed_rows = []
        self.raw_text = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.ignored += 1
            return
        if self.ignored:
            return
        if tag == 'tr':
            self.row = []
            self.row_details = []
        elif tag in ('th', 'td') and self.row is not None:
            self.cell = []
            self.cell_attrs = dict(attrs)

    def handle_data(self, data):
        if not self.ignored:
            self.raw_text.append(data)
            if self.cell is not None:
                self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.ignored = max(0, self.ignored - 1)
            return
        if self.ignored:
            return
        if tag in ('th', 'td') and self.cell is not None and self.row is not None:
            value = ' '.join(' '.join(self.cell).split())
            self.row.append(value)
            self.row_details.append((value, self.cell_attrs or {}))
            self.cell = None
            self.cell_attrs = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.detailed_rows.append(self.row_details)
            self.row = None
            self.row_details = None


def num(text):
    if isinstance(text, (int, float)):
        text = str(text)
    text = str(text or '').strip().replace(',', '')
    if not re.fullmatch(r'\d+(?:\.\d+)?', text):
        return None
    return float(text)


def pair(code, buy, sell):
    buy, sell = num(buy), num(sell)
    if buy is None or sell is None or sell < buy:
        return None
    low, high = (15, 65) if code == 'USD' else (0.4, 7)
    if not (low <= buy <= high and low <= sell <= high and sell <= buy * 1.2):
        return None
    return {'buy': buy, 'sell': sell}


def stamp_from_text(text):
    pat = r'(?:牌價最新掛牌時間|掛牌時間)\s*[：:]?\s*(20\d\d)[/\-](\d{1,2})[/\-](\d{1,2})\s*(\d{1,2}):(\d\d)'
    m = re.search(pat, text)
    if not m:
        return ''
    y, mo, d, hh, mm = map(int, m.groups())
    iso = datetime(y, mo, d, hh, mm, tzinfo=TW)
    # Parser supports archived samples too; live freshness is checked in main().
    return iso.isoformat(timespec='seconds')


def from_html(payload):
    parser = TableReader()
    parser.feed(payload.decode('utf-8-sig', errors='replace'))
    stamp = stamp_from_text(' '.join(parser.raw_text))
    result = {}
    for row, details in zip(parser.rows, parser.detailed_rows):
        if len(row) < 5:
            continue
        for code in CODES:
            for i, cell in enumerate(row[:3]):
                if not re.search(r'(?<![A-Z])' + code + r'(?![A-Z])', cell, re.I):
                    continue
                spot_buy = spot_sell = None
                # The official BOT board identifies spot-rate cells with
                # data-table="本行即期買入/賣出" or CSS class "rate-content-sight".
                # Prefer these labels to avoid accidentally reading duplicate
                # print-only table cells or cash quotes.
                for value, attrs in details:
                    label = (attrs.get('data-table') or '') + ' ' + (attrs.get('class') or '')
                    if ('即期買入' in label or '即期買進' in label):
                        spot_buy = value
                    elif ('即期賣出' in label):
                        spot_sell = value
                if spot_buy is None or spot_sell is None:
                    sight = [(value, attrs) for value, attrs in details
                             if 'rate-content-sight' in (attrs.get('class') or '')]
                    if len(sight) >= 2:
                        spot_buy, spot_sell = sight[0][0], sight[1][0]
                # Stable, documented order in BOT table:
                # currency, cash-buy, cash-sell, spot-buy, spot-sell.
                if spot_buy is None or spot_sell is None:
                    if len(row) >= i + 5:
                        spot_buy, spot_sell = row[i + 3], row[i + 4]
                candidate = pair(code, spot_buy, spot_sell)
                if candidate:
                    result[code] = {**candidate, 'quotedAt': stamp}
    if not result:
        raise ValueError('找不到 USD/ZAR 即期牌價欄位')
    return result


def from_text(payload):
    """Parse BOT's official /xrt/fltxt/0/day, NOT cash exchange rates.

    Example (simplified):
    USD Buying 32.54500 32.87000 ... Selling 33.21500 33.02000 ...
        ^cash buy ^SPOT buy            ^cash sell ^SPOT sell
    ZAR cash quotes are zero, but its *spot* buy/sell must be nonzero.
    The text endpoint does not include a published quote timestamp; do not
    invent one. The JSON will show the actual successful fetch time instead.
    """
    raw = None
    for encoding in ('utf-8-sig', 'cp950', 'big5'):
        try:
            raw = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if raw is None:
        raise ValueError('文字牌告編碼無法解析')
    rates = {}
    for line in raw.splitlines():
        columns = line.split()
        if len(columns) < 7:
            continue
        code = columns[0].upper()
        if code not in CODES or columns[1].lower() != 'buying':
            continue
        try:
            selling_idx = next(i for i, s in enumerate(columns) if s.lower() == 'selling')
        except StopIteration:
            continue
        # buying: [currency, Buying, cash-buy, spot-buy, ...]
        # selling: [..., Selling, cash-sell, spot-sell, ...]
        if selling_idx + 2 >= len(columns):
            continue
        matched = pair(code, columns[3], columns[selling_idx + 2])
        if matched:
            rates[code] = {**matched, 'quotedAt': ''}
    if not rates:
        raise ValueError('官方文字牌告未找到 USD / ZAR 即期買賣價')
    return rates


def from_csv(payload):
    raw = None
    for enc in ('utf-8-sig', 'cp950', 'big5'):
        try:
            raw = payload.decode(enc)
            break
        except UnicodeDecodeError:
            pass
    if raw is None:
        raise ValueError('無法解析牌告 CSV 編碼')
    result = {}
    for row in csv.reader(io.StringIO(raw)):
        if len(row) < 5:
            continue
        for code in CODES:
            if re.search(r'(?<![A-Z])'+code+r'(?![A-Z])', row[0], re.I):
                candidate = pair(code, row[3], row[4])
                if candidate:
                    result[code] = {**candidate, 'quotedAt': ''}
    if not result:
        raise ValueError('牌告 CSV 沒有 USD/ZAR 即期買賣價')
    return result


def fetch(url):
    req = Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36',
        'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
        'Accept': 'text/html,text/csv,*/*',
        'Cache-Control': 'no-cache',
    })
    with urlopen(req, timeout=25) as r:
        raw = r.read(2_000_000)
    if len(raw) < 100:
        raise ValueError('牌告網站沒有回傳有效資料')
    return raw


def make_doc(existing, fresh, source_by_code, errors, checked):
    last = existing.get('rates', {}) if isinstance(existing, dict) else {}
    records = {}
    for code in CODES:
        if code in fresh:
            records[code] = {**fresh[code], 'fetchedAt': checked, 'status': 'found',
                             'sourceName': source_by_code[code][0], 'sourceUrl': source_by_code[code][1]}
        else:
            old = last.get(code) or {}
            old_pair = pair(code, old.get('buy'), old.get('sell'))
            records[code] = {**(old_pair or {'buy': None, 'sell': None}),
                             'quotedAt': old.get('quotedAt') or '',
                             'fetchedAt': old.get('fetchedAt') or '',
                             'status': 'cached' if old_pair else 'unavailable',
                             'sourceName': old.get('sourceName') or '臺灣銀行',
                             'sourceUrl': old.get('sourceUrl') or RATE_PAGE}
    okay = len(fresh) == len(CODES)
    msg = ('兩種匯率均已更新' if okay else
           '部分匯率沒有更新' if fresh else '臺灣銀行資料目前無法擷取')
    if errors:
        msg += '。' + '；'.join(errors)[-1200:]
    return {'schemaVersion': 1, 'checkedAt': checked, 'source': '臺灣銀行',
            'sourceUrl': RATE_PAGE, 'sourceUsed': ', '.join(dict.fromkeys(x[1] for x in source_by_code.values())),
            'ok': okay, 'message': msg, 'rates': records}


def main():
    print(f"[FX script] version={SCRIPT_VERSION} source_first={RATE_TEXT}", flush=True)
    try:
        old = json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else {}
    except (OSError, json.JSONDecodeError):
        old = {}
    checked = datetime.now(TW).isoformat(timespec='seconds')
    fresh, provenance, errors = {}, {}, []
    for title, url, format_name in SOURCES:
        if len(fresh) == len(CODES):
            break
        try:
            payload = fetch(url)
            if format_name == 'text':
                found = from_text(payload)
            elif format_name == 'csv':
                found = from_csv(payload)
            else:
                found = from_html(payload)
                for code, rate in found.items():
                    quoted = rate.get('quotedAt')
                    if quoted:
                        dt = datetime.fromisoformat(quoted)
                        # Reject old cached server pages; weekends/holidays can
                        # naturally retain the last trading day's board.
                        if not datetime.now(TW) - timedelta(days=8) <= dt <= datetime.now(TW) + timedelta(hours=2):
                            raise ValueError('官方網頁牌告日期過舊或來自未來：' + quoted)
            for code in CODES:
                if code in found and code not in fresh:
                    fresh[code] = found[code]
                    provenance[code] = (title, url)
            print(f'[FX source] {title}: '+ ', '.join(sorted(found)), flush=True)
        except Exception as exc:
            message = f'{title} ({url})：{type(exc).__name__}: {str(exc)[:220]}'
            errors.append(message)
            print(f'::warning::{message}', flush=True)
        time.sleep(0.2)
    data = make_doc(old, fresh, provenance, errors, checked)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('匯率檢查時間:', checked, flush=True)
    for code, rate in data['rates'].items():
        print(f'[FX] {code}: spot_buy={rate["buy"]}, spot_sell={rate["sell"]}, status={rate["status"]}, '
              f'quotedAt={rate["quotedAt"]}, fetchedAt={rate["fetchedAt"]}', flush=True)
    print('[FX status]', data['message'], flush=True)
    if not data['ok']:
        print('::warning::兩種即期匯率沒有全部成功更新；已保留舊牌價與錯誤訊息。', flush=True)
    return 0



# Backward-compatible names used by the previously uploaded tests/test_update_fx.py.
# The current parser API uses from_html/from_csv/pair/make_doc. Keep these adapters
# while older tests remain in the repository so the CI test discovery does not fail
# during import. All adapters call the real parsers/validation; no dummy values.
def parse_bank_text(blob):
    return from_text(blob)


def parse_bank_html(blob):
    return from_html(blob)


def parse_bank_csv(blob):
    return from_csv(blob)


def rate_pair(buy, sell):
    # Legacy tests use a currency-neutral validator. ZAR gives a conservative
    # acceptable range; for USD quotes accept the normal USD band instead.
    return pair('ZAR', buy, sell) or pair('USD', buy, sell)


def build_document(existing, fetched, url='', message='', at=None):
    # Preserve the existing fresh/cached/missing distinctions and timestamps.
    at = at or datetime.now(TW).isoformat(timespec='seconds')
    provenance = {code: ('臺灣銀行即期牌告', url or RATE_PAGE)
                  for code in (fetched or {}) if code in CODES}
    errors = [message] if message and not fetched else []
    return make_doc(existing, fetched or {}, provenance, errors, at)


if __name__ == '__main__':
    sys.exit(main())

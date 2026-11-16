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
RATE_PAGE = 'https://rate.bot.com.tw/xrt?Lang=zh-TW'
SOURCES = (
    ('臺灣銀行即期牌告', RATE_PAGE, 'html'),
    ('臺灣銀行即期牌告（備援頁）', 'https://rate.bot.com.tw/xrt?mode=desktop', 'html'),
    ('臺灣銀行牌告 CSV', 'https://rate.bot.com.tw/xrt/flcsv/0/day', 'csv'),
    ('臺灣銀行前一交易日收盤牌告', 'https://rate.bot.com.tw/xrt/all/day', 'html'),
)


class TableReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None
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
        elif tag in ('th', 'td') and self.row is not None:
            self.cell = []

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
            self.row.append(' '.join(' '.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None


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
    if iso > datetime.now(TW) + timedelta(days=1) or iso < datetime.now(TW) - timedelta(days=20):
        raise ValueError('臺灣銀行牌告日期不在合理範圍')
    return iso.isoformat(timespec='seconds')


def from_html(payload):
    parser = TableReader()
    parser.feed(payload.decode('utf-8-sig', errors='replace'))
    stamp = stamp_from_text(' '.join(parser.raw_text))
    result = {}
    for row in parser.rows:
        # Currency + cash buy + cash sell + spot buy + spot sell.
        # Keep '-' placeholders in cash fields (ZAR has no cash quotes).
        if len(row) < 5:
            continue
        for code in CODES:
            for i, cell in enumerate(row[:3]):
                if re.search(r'(?<![A-Z])' + code + r'(?![A-Z])', cell, re.I) and len(row) >= i + 5:
                    candidate = pair(code, row[i+3], row[i+4])
                    if candidate:
                        result[code] = {**candidate, 'quotedAt': stamp}
    if not result:
        raise ValueError('找不到 USD/ZAR 即期牌價欄位')
    return result


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
    try:
        old = json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else {}
    except (OSError, json.JSONDecodeError):
        old = {}
    checked = datetime.now(TW).isoformat(timespec='seconds')
    fresh, provenance, errors = {}, {}, []
    for title, url, format_name in SOURCES:
        if len(fresh) == 2:
            break
        try:
            found = from_csv(fetch(url)) if format_name == 'csv' else from_html(fetch(url))
            for code, quote in found.items():
                if code not in fresh:
                    fresh[code] = quote
                    provenance[code] = (title, url)
        except Exception as e:
            errors.append(f'{title}：{type(e).__name__}: {str(e)[:155]}')
        time.sleep(0.15)
    data = make_doc(old, fresh, provenance, errors, checked)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('匯率最後檢查：', data['checkedAt'])
    for code, quote in data['rates'].items():
        print(code, '即期買進=', quote['buy'], '即期賣出=', quote['sell'], '狀態=', quote['status'],
              '牌告時間=', quote['quotedAt'], '來源=', quote['sourceUrl'])
    print('詳細狀態：', data['message'])
    # Always publish diagnostics, even if official site is temporarily inaccessible.
    return 0


if __name__ == '__main__':
    sys.exit(main())

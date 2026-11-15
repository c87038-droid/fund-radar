#!/usr/bin/env python3
"""Update USD/ZAR spot BUY/SELL quotes from Bank of Taiwan's public board.

The rates are the bank's perspective and quoted in TWD per 1 foreign currency.
Never substitutes market mid-rates for actual bank spreads. Failed fetches keep
old values visibly marked 'cached', never mark a fetch as a fresh quote.
Python standard library only; independent of the mutual-fund NAV updater.
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

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'data' / 'fx.json'
TAIWAN = timezone(timedelta(hours=8))
CURRENCIES = ('USD', 'ZAR')
SOURCE_HTML = 'https://rate.bot.com.tw/xrt?Lang=zh-TW'
SOURCE_CSV = 'https://rate.bot.com.tw/xrt/flcsv/0/day'


class BankTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None
        self.cell_tag = None
        self.ignore = 0
        self.all_text = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.ignore += 1
            return
        if self.ignore:
            return
        if tag == 'tr':
            self.row = []
        elif tag in ('th', 'td') and self.row is not None:
            self.cell_tag = tag
            self.cell = []

    def handle_data(self, data):
        if self.ignore:
            return
        self.all_text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            if self.ignore:
                self.ignore -= 1
            return
        if self.ignore:
            return
        if tag in ('th', 'td') and self.cell is not None and self.row is not None:
            self.row.append(re.sub(r'\s+', ' ', ' '.join(self.cell)).strip())
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def rate_number(value):
    value = str(value or '').strip().replace(',', '')
    if not re.fullmatch(r'\d+(?:\.\d+)?', value):
        return None
    number = float(value)
    return number if 0.01 < number < 200 else None


def rate_pair(buy, sell):
    buy, sell = rate_number(buy), rate_number(sell)
    if buy is None or sell is None or sell < buy or sell > buy * 1.2:
        return None
    return {'buy': buy, 'sell': sell}


def parse_bank_html(blob):
    parser = BankTable()
    parser.feed(blob.decode('utf-8-sig'))
    text = ' '.join(parser.all_text)
    stamp = re.search(r'牌價最新掛牌時間\s*[：:]\s*(20\d{2})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}:\d{2})', text)
    quote_at = ''
    if stamp:
        y, m, d, hm = stamp.groups()
        quote_at = f'{y}-{int(m):02d}-{int(d):02d}T{hm}:00+08:00'
        # Never accept a future-dated official board or a wildly old response.
        parsed = datetime.fromisoformat(quote_at)
        if parsed > datetime.now(TAIWAN) + timedelta(days=1) or parsed < datetime.now(TAIWAN) - timedelta(days=15):
            raise ValueError('臺銀牌價日期異常，未採用')
    results = {}
    for cells in parser.rows:
        if len(cells) < 5:
            continue
        for currency in CURRENCIES:
            indices = [i for i, s in enumerate(cells[:3]) if re.search(r'(?<![A-Z])'+currency+r'(?![A-Z])', s, re.I)]
            if not indices:
                continue
            start = indices[0]
            # Standard BOT board: currency, cash buy, cash sell, spot buy, spot sell.
            # Keep '-' placeholders, otherwise the ZAR spot pair would shift.
            following = cells[start+1:start+5]
            if len(following) < 4:
                continue
            result = rate_pair(following[2], following[3])
            if result:
                results[currency] = {**result, 'quotedAt': quote_at}
    if set(results) != set(CURRENCIES):
        raise ValueError('臺銀牌價網頁缺少美元或南非幣的即期買賣價')
    return results


def decode_csv(blob):
    for encoding in ('utf-8-sig', 'cp950', 'big5'):
        try:
            return blob.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError('臺銀 CSV 無法解碼')


def parse_bank_csv(blob):
    content = decode_csv(blob)
    table = list(csv.reader(io.StringIO(content)))
    results = {}
    for row in table:
        if len(row) < 5:
            continue
        first = row[0].strip().upper()
        for code in CURRENCIES:
            if not re.search(r'(?<![A-Z])'+code+r'(?![A-Z])', first):
                continue
            # The published CSV columns are currency, cash buy/sell, spot buy/sell.
            # The CSV endpoint does not guarantee a bank quote timestamp.
            pair = rate_pair(row[3], row[4])
            if pair:
                results[code] = {**pair, 'quotedAt': ''}
    if set(results) != set(CURRENCIES):
        raise ValueError('臺銀 CSV 找不到 USD/ZAR 正確即期買賣價')
    return results


def fetch(url):
    req = Request(url, headers={'User-Agent':'Mozilla/5.0 (compatible; FundRadar/3.0)', 'Accept':'text/html,text/csv,*/*'})
    with urlopen(req, timeout=28) as result:
        payload = result.read(3_000_000)
    if len(payload) < 100:
        raise ValueError('臺銀回傳資料過短')
    return payload


def build_document(existing, fetched, url='', message='', at=None):
    at = at or datetime.now(TAIWAN).isoformat(timespec='seconds')
    prior = existing.get('rates', {}) if isinstance(existing, dict) else {}
    current = {}
    for code in CURRENCIES:
        latest = fetched.get(code) if fetched else None
        if latest:
            current[code] = {
                'buy': latest['buy'], 'sell': latest['sell'],
                'quotedAt': latest.get('quotedAt',''),
                'fetchedAt': at, 'status': 'found'
            }
        else:
            old = prior.get(code, {})
            pair = rate_pair(old.get('buy'), old.get('sell'))
            current[code] = {**(pair or {'buy':None,'sell':None}),
                'quotedAt': old.get('quotedAt',''),
                'fetchedAt': old.get('fetchedAt',''),
                'status': 'cached' if pair else 'unavailable'}
    return {'schemaVersion':1, 'checkedAt':at, 'source':'臺灣銀行',
            'sourceUrl':SOURCE_HTML, 'sourceUsed':url or existing.get('sourceUsed',''),
            'ok':bool(fetched), 'message':message, 'rates':current}


def main():
    existing = json.loads(OUTPUT.read_text(encoding='utf-8')) if OUTPUT.exists() else {}
    errors = []
    data = None
    used = ''
    for url, parser in ((SOURCE_HTML, parse_bank_html), (SOURCE_CSV, parse_bank_csv)):
        try:
            data = parser(fetch(url))
            used = url
            break
        except Exception as exc:
            errors.append(f'{url}: {type(exc).__name__} {str(exc)[:150]}')
            time.sleep(0.4)
    message = '臺灣銀行即期匯率已讀取' if data else '；'.join(errors)
    document = build_document(existing, data, used, message)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for code, item in document['rates'].items():
        print(f'{code}: 銀行買進={item["buy"]} 賣出={item["sell"]} 狀態={item["status"]} 牌告時間={item["quotedAt"]}')
    if errors:
        print('匯率來源檢查：' + '；'.join(errors), file=sys.stderr)
    print('已更新 data/fx.json；未取得的匯率不會填入猜測值。')


if __name__ == '__main__':
    main()

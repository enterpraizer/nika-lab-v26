"""Источник агента ЛР4: HTML-страница рейтинга языков программирования TIOBE.

Здесь только работа с источником и вывод — без sc-машины, поэтому всё проверяется юнит-тестами:
скачивание страницы, разбор таблиц рейтинга, поиск языка, класс популярности и подпись записи.
"""
import os
import re
import urllib.request
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple, Optional

TIOBE_URL = os.environ.get("TIOBE_URL", "https://www.tiobe.com/tiobe-index/")
SNAPSHOT_FILE = Path(__file__).resolve().parent / "data" / "tiobe_snapshot.html"
USER_AGENT = "Mozilla/5.0 (compatible; NIKA-lab-agent/1.0; BSUIR TIISPIS lab 4)"  # заголовки HTTP — только latin-1
TIMEOUT_SECONDS = 7  # программа обработки сообщений ждёт шаг агента ограниченное время
POPULAR_TOP = 10
RATING_SIZE = 50
TABLES = ("top20", "otherPL")  # места 1-20 и 21-50
SHARE = re.compile(r"^\d+(\.\d+)?%$")
MONTHS = {"January": "январь", "February": "февраль", "March": "март", "April": "апрель", "May": "май",
          "June": "июнь", "July": "июль", "August": "август", "September": "сентябрь", "October": "октябрь",
          "November": "ноябрь", "December": "декабрь"}


class Entry(NamedTuple):
    position: int
    name: str
    share: str


class Rating(NamedTuple):
    month: str            # «October 2026», как на странице
    entries: dict         # название языка в рейтинге -> Entry
    skipped: list         # строки таблиц, которые не удалось разобрать


class _TableRows(HTMLParser):
    """Собирает текст ячеек строк <tbody> таблиц с нужными id."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.found = [], set()
        self._table = self._row = self._cell = None
        self._in_body = False

    def handle_starttag(self, tag, attrs):
        if tag == "table" and dict(attrs).get("id") in TABLES:
            self._table = dict(attrs)["id"]
            self.found.add(self._table)
        elif self._table and tag == "tbody":
            self._in_body = True
        elif self._table and self._in_body and tag == "tr":
            self._row = []
        elif self._row is not None and tag == "td":
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append((self._table, self._row))
            self._row = None
        elif tag == "tbody":
            self._in_body = False
        elif tag == "table":
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_rating(html: str) -> Rating:
    """Разобрать страницу рейтинга.

    ValueError, если нет месяца или таблиц, или если таблицы не похожи на рейтинг: в top20 должны быть
    ровно места 1-20, в otherPL — не меньше 25 мест из 21-50, места и названия без повторов. Так сдвиг
    столбцов после смены вёрстки не превратится в неверный ответ, а приведёт к сохранённой копии.
    """
    month = re.search(r"TIOBE Index for ([A-Z][a-z]+ \d{4})", unescape(html))
    if not month:
        raise ValueError("на странице не найден месяц рейтинга («TIOBE Index for …»)")
    month_ru(month.group(1))  # непонятный месяц — ошибка разбора, а не ошибка позже, при погружении
    parser = _TableRows()
    parser.feed(html)
    missing = [t for t in TABLES if t not in parser.found]
    if missing:
        raise ValueError(f"на странице нет таблиц рейтинга: {', '.join(missing)}")
    entries, skipped, by_table = {}, [], {"top20": [], "otherPL": []}
    for table, cells in parser.rows:
        # top20: место, место год назад, изменение, значок, язык, доля, изменение; otherPL: место, язык, доля
        position, name, share = (cells[0], cells[4], cells[5]) if table == "top20" and len(cells) >= 6 else \
            (cells[0], cells[1], cells[2]) if table == "otherPL" and len(cells) >= 3 else (None, None, None)
        if position is None or not position.isdigit() or not name or "%" in name or not SHARE.match(share):
            skipped.append(f"{table}: {' | '.join(cells)}")
            continue
        if name in entries:
            raise ValueError(f"язык {name} встречается в рейтинге дважды")
        entries[name] = Entry(int(position), name, share)
        by_table[table].append(int(position))
    if sorted(by_table["top20"]) != list(range(1, 21)):
        raise ValueError(f"в таблице top20 не места 1-20: {sorted(by_table['top20'])[:25]}")
    other = by_table["otherPL"]
    if len(other) < 25 or len(set(other)) != len(other) or not all(21 <= p <= RATING_SIZE for p in other):
        raise ValueError(f"таблица otherPL не похожа на места 21-{RATING_SIZE}: разобрано {len(other)}")
    return Rating(month.group(1), entries, skipped)


def find_entry(rating: Rating, tiobe_name: str) -> Optional[Entry]:
    return rating.entries.get(tiobe_name)


def popularity(entry: Optional[Entry]) -> str:
    """Класс популярности: первая десятка — popular, места 11-50 — less_popular, иначе outside."""
    if entry is None or entry.position > RATING_SIZE:
        return "outside"
    return "popular" if entry.position <= POPULAR_TOP else "less_popular"


def month_ru(month: str) -> str:
    """«October 2026» -> «октябрь 2026 года»."""
    name, _, year = month.partition(" ")
    if name not in MONTHS or not year.isdigit():
        raise ValueError(f"непонятный месяц рейтинга: {month}")
    return f"{MONTHS[name]} {year} года"


def entry_title(entry: Entry, month: str) -> str:
    """Русское название записи рейтинга: «1 место (рейтинг за октябрь 2026 года, доля 19,23 %)»."""
    share = entry.share.replace("%", "").strip().replace(".", ",")
    return f"{entry.position} место (рейтинг за {month_ru(month)}, доля {share} %)"


def download(url: str, timeout: float = TIMEOUT_SECONDS) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def read_page(url: str = TIOBE_URL, timeout: float = TIMEOUT_SECONDS):
    """(рейтинг, источник): со страницы url, а если она недоступна или не разбирается — из сохранённой копии.

    Источник — url или «snapshot». Ошибка копии не перехватывается: без неё агенту ответить нечем.
    """
    try:
        return parse_rating(download(url, timeout)), url
    except Exception as error:  # noqa: BLE001 — любая ошибка сети или разбора ведёт к сохранённой копии
        rating = parse_rating(SNAPSHOT_FILE.read_text(encoding="utf-8"))
        return rating._replace(skipped=rating.skipped + [f"страница {url} не прочитана: {error}"]), "snapshot"

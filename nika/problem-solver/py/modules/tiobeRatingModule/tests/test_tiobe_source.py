"""Юнит-тесты разбора страницы рейтинга TIOBE (без sc-машины и без сети).

Запуск из папки модуля: python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tiobe_source import (SNAPSHOT_FILE, USER_AGENT, Entry, entry_title, find_entry, month_ru,  # noqa: E402
                          parse_rating, popularity, read_page)

SNAPSHOT = SNAPSHOT_FILE.read_text(encoding="utf-8")


def page(month="October 2026", names=None, drop=(), extra_other="", shift=False):
    """Учебная страница рейтинга: места 1-50, языки Lang1…Lang50 (names заменяет названия по месту)."""
    names = {**{p: f"Lang{p}" for p in range(1, 51)}, **(names or {})}
    pad = "<td></td>" if shift else ""  # сдвиг столбцов, как после смены вёрстки
    top = "".join(f"<tr><td>{p}</td><td>{p}</td><td></td>{pad}<td class=\"td-top20\"></td><td>{names[p]}</td><td>{30 - p}.5%</td><td>+0.1%</td></tr>"
                  for p in range(1, 21) if p not in drop)
    other = "".join(f"<tr><td>{p}</td><td>{names[p]}</td><td>0.{60 - p}%</td></tr>" for p in range(21, 51) if p not in drop)
    return (f"<h1><b>TIOBE Index for {month}</b></h1>"
            f"<table id=\"top20\"><thead><tr><th>a</th></tr></thead><tbody>{top}</tbody></table>"
            f"<table id=\"otherPL\"><thead><tr><th>Position</th></tr></thead><tbody>{other}{extra_other}</tbody></table>")


class ParseSnapshotTest(unittest.TestCase):
    def test_snapshot_has_fifty_positions(self):
        rating = parse_rating(SNAPSHOT)
        self.assertEqual(rating.month, "October 2026")
        self.assertEqual(sorted(e.position for e in rating.entries.values()), list(range(1, 51)))

    def test_languages_of_the_subject_domain(self):
        rating = parse_rating(SNAPSHOT)
        expected = {"Python": (1, "19.23%"), "C": (2, "11.33%"), "C++": (3, "8.87%"), "Java": (4, "8.41%"),
                    "JavaScript": (6, "2.77%"), "Assembly language": (23, "0.76%"), "Prolog": (31, "0.49%"),
                    "Haskell": (41, "0.33%")}
        for name, (position, share) in expected.items():
            self.assertEqual(find_entry(rating, name), Entry(position, name, share), name)

    def test_entities_in_names_are_decoded(self):
        rating = parse_rating(SNAPSHOT)
        self.assertIsNotNone(find_entry(rating, "(Visual) FoxPro"))
        self.assertIsNotNone(find_entry(rating, "Delphi/Object Pascal"))


class ParseErrorsTest(unittest.TestCase):
    def test_page_without_tables_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "таблиц"):
            parse_rating("<h1><b>TIOBE Index for October 2026</b></h1><p>Новая вёрстка</p>")

    def test_page_without_month_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "месяц"):
            parse_rating(page().replace("TIOBE Index for", "Index"))

    def test_unknown_month_is_rejected_while_parsing(self):
        with self.assertRaisesRegex(ValueError, "месяц"):
            parse_rating(page(month="Sept 2026"))

    def test_shifted_columns_are_rejected(self):
        # лишний столбец в top20: на месте названия оказалась бы пустая ячейка, на месте доли — название
        with self.assertRaisesRegex(ValueError, "top20"):
            parse_rating(page(shift=True))

    def test_duplicate_language_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "дважды"):
            parse_rating(page(names={2: "Lang1"}))

    def test_bad_row_is_skipped(self):
        rating = parse_rating(page(extra_other="<tr><td>x</td><td>Bad</td><td>0.1%</td></tr>"))
        self.assertEqual(len(rating.entries), 50)
        self.assertNotIn("Bad", rating.entries)
        self.assertEqual(len(rating.skipped), 1)

    def test_missing_rows_of_top20_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "top20"):
            parse_rating(page(drop=(7,)))


class DerivationTest(unittest.TestCase):
    def test_missing_language(self):
        rating = parse_rating(page(names={41: "Haskell"}, drop=(41,)))
        self.assertEqual(len(rating.entries), 49)
        self.assertIsNone(find_entry(rating, "Haskell"))
        self.assertEqual(popularity(None), "outside")

    def test_border_between_classes(self):
        self.assertEqual(popularity(Entry(1, "Python", "19%")), "popular")
        self.assertEqual(popularity(Entry(10, "Rust", "1%")), "popular")
        self.assertEqual(popularity(Entry(11, "Go", "1%")), "less_popular")
        self.assertEqual(popularity(Entry(50, "Awk", "0.25%")), "less_popular")
        self.assertEqual(popularity(Entry(51, "X", "0.1%")), "outside")

    def test_russian_month(self):
        self.assertEqual(month_ru("October 2026"), "октябрь 2026 года")
        self.assertEqual(month_ru("May 2025"), "май 2025 года")
        with self.assertRaises(ValueError):
            month_ru("Октябрь 2026")

    def test_entry_title(self):
        self.assertEqual(entry_title(Entry(1, "Python", "19.23%"), "October 2026"),
                         "1 место (рейтинг за октябрь 2026 года, доля 19,23 %)")


class ReadPageTest(unittest.TestCase):
    def test_user_agent_fits_http_header(self):
        USER_AGENT.encode("latin-1")  # иначе urllib падает до отправки запроса, и агент всегда берёт копию

    def test_unreachable_site_falls_back_to_snapshot(self):
        rating, source = read_page("http://127.0.0.1:9/tiobe-index/", timeout=2)
        self.assertEqual(source, "snapshot")
        self.assertEqual(find_entry(rating, "Python").position, 1)

    def test_broken_page_falls_back_to_snapshot(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "broken_page.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write("<html><body>Сайт переделали</body></html>")
        try:
            rating, source = read_page("file://" + path, timeout=2)
        finally:
            os.remove(path)
        self.assertEqual(source, "snapshot")
        self.assertEqual(len(rating.entries), 50)

    def test_local_page_is_used_when_it_parses(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "small_page.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(page(month="May 2025"))
        try:
            rating, source = read_page("file://" + path, timeout=2)
        finally:
            os.remove(path)
        self.assertEqual(source, "file://" + path)
        self.assertEqual(rating.month, "May 2025")


if __name__ == "__main__":
    unittest.main()

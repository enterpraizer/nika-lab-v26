"""
Агент рейтинга TIOBE.

Лабораторная работа №4 по курсу ТиИСПИС, вариант 26 «Языки программирования».
Вариант агента: web-страница (скрапер) · полуструктурированные данные HTML ·
процедурная (алгоритмическая) модель обработки · Python.

Условие инициирования: появление дуги принадлежности действия классу
action_find_tiobe_rating. Действие порождает программа обработки сообщений
message_processing_program; аргумент действия — сообщение пользователя.

Результат: место языка, выделенного в сообщении, погружено в базу знаний
(запись класса concept_tiobe_rating_entry, связанная с языком отношением
nrel_tiobe_rating_entry), и на нём выведено новое знание — принадлежность языка
одному из классов concept_popular_language, concept_less_popular_language,
concept_language_outside_tiobe_top50.
"""

import logging
import time

from sc_client.client import delete_elements, template_search
from sc_client.constants import sc_types
from sc_client.models import ScAddr, ScLinkContentType, ScTemplate

from sc_kpm import ScAgentClassic, ScKeynodes, ScResult
from sc_kpm.utils import (check_edge, create_edge, create_link, create_node, delete_edges,
                          get_element_by_norole_relation, get_link_content_data, get_system_idtf)
from sc_kpm.utils.action_utils import create_action_answer, finish_action_with_status, get_action_arguments

from .tiobe_source import POPULAR_TOP, RATING_SIZE, TIOBE_URL, entry_title, find_entry, popularity, read_page

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(name)s | %(message)s", datefmt="[%d-%b-%y %H:%M:%S]"
)

ACTION_CLASS = "action_find_tiobe_rating"
MESSAGE_CLASSES = ("concept_message_about_tiobe_place", "concept_message_about_popularity")
ENTRY_CLASS = "concept_tiobe_rating_entry"
POPULARITY_CLASSES = {
    "popular": "concept_popular_language",
    "less_popular": "concept_less_popular_language",
    "outside": "concept_language_outside_tiobe_top50",
}
CACHE_SECONDS = 3600  # страница рейтинга обновляется раз в месяц: чаще часа сайт не тревожим
RETRY_SECONDS = 300   # после неудачи сайт пробуем снова через 5 минут, а до того отвечаем по копии без ожидания


class TiobeRatingAgent(ScAgentClassic):
    """Sc-агент: скачивает страницу TIOBE, погружает место языка и выводит класс популярности."""

    def __init__(self):
        super().__init__(ACTION_CLASS)
        self._cache = None  # (время, рейтинг, источник)

    def on_event(self, event_element: ScAddr, event_edge: ScAddr, action_element: ScAddr) -> ScResult:
        result = self.run(action_element)
        is_successful = result == ScResult.OK
        finish_action_with_status(action_element, is_successful)
        self.logger.info("TiobeRatingAgent finished %s", "successfully" if is_successful else "unsuccessfully")
        return result

    # Процедура 1. Точка входа: проверка применимости и общий ход работы
    def run(self, action_node: ScAddr) -> ScResult:
        self.logger.info("TiobeRatingAgent started")
        try:
            arguments = get_action_arguments(action_node, 1)
            if not arguments or not arguments[0].is_valid():
                self.logger.info("TiobeRatingAgent: the action has no message")
                return ScResult.OK
            message = arguments[0]
            if not any(check_edge(sc_types.EDGE_ACCESS_VAR_POS_PERM, ScKeynodes.resolve(cls, sc_types.NODE_CONST_CLASS), message)
                       for cls in MESSAGE_CLASSES):
                self.logger.info("TiobeRatingAgent: the message isn't about the TIOBE rating")
                return ScResult.OK

            language = self.get_entity(message)
            if not language.is_valid():
                self.logger.info("TiobeRatingAgent: the message has no entity")
                return ScResult.OK
            language_idtf = get_system_idtf(language)
            tiobe_name = self.get_tiobe_name(language)
            if not tiobe_name:
                self.logger.info("TiobeRatingAgent: %s has no nrel_tiobe_name", language_idtf)
                return ScResult.OK

            rating = self.read_page()
            entry = find_entry(rating, tiobe_name)
            entry_node = self.immerse_entry(language, entry, rating.month)
            status = self.derive_popularity(language, entry)
            create_action_answer(action_node, entry_node if entry_node else language)
            self.logger.info("TiobeRatingAgent: %s (%s) -> %s, %s", language_idtf, tiobe_name,
                             f"position {entry.position}" if entry else "not in the rating", status)
            return ScResult.OK
        except Exception as error:  # noqa: BLE001 — агент не должен ронять программу обработки сообщений
            self.logger.info("TiobeRatingAgent: finished with an error: %s", error)
            return ScResult.ERROR

    # Процедура 2. Сущность, выделенная в сообщении правилом распознавания
    def get_entity(self, message: ScAddr) -> ScAddr:
        rrel_entity = ScKeynodes.resolve("rrel_entity", sc_types.NODE_CONST_ROLE)
        template = ScTemplate()
        template.triple_with_relation(
            message, sc_types.EDGE_ACCESS_VAR_POS_PERM, sc_types.NODE_VAR, sc_types.EDGE_ACCESS_VAR_POS_PERM, rrel_entity)
        results = template_search(template)
        return results[0][2] if results else ScAddr(0)

    # Процедура 3. Название языка в рейтинге — из базы знаний
    def get_tiobe_name(self, language: ScAddr) -> str:
        nrel = ScKeynodes.resolve("nrel_tiobe_name", sc_types.NODE_CONST_NOROLE)
        link = get_element_by_norole_relation(src=language, nrel_node=nrel)
        name = get_link_content_data(link) if link.is_valid() else ""
        self.logger.info("TiobeRatingAgent: get_tiobe_name -> %s", name or "none")
        return name

    # Процедура 4. Чтение и разбор web-страницы (с кэшем и запасной копией)
    def read_page(self):
        if self._cache and time.time() - self._cache[0] < (RETRY_SECONDS if self._cache[2] == "snapshot" else CACHE_SECONDS):
            _, rating, source = self._cache
            self.logger.info("TiobeRatingAgent: read_page %s (cached), %s, %d positions", source, rating.month, len(rating.entries))
            return rating
        rating, source = read_page(TIOBE_URL)
        for note in rating.skipped:
            self.logger.info("TiobeRatingAgent: read_page skipped %s", note)
        self.logger.info("TiobeRatingAgent: read_page %s, %s, %d positions", source, rating.month, len(rating.entries))
        self._cache = (time.time(), rating, source)
        return rating

    # Процедура 5. Погружение записи рейтинга в базу знаний
    def immerse_entry(self, language: ScAddr, entry, month: str):
        """Заменить прежнюю запись рейтинга языка новой. Без места в рейтинге запись только снимается."""
        removed = 0
        for result in self.norole_results(language, "nrel_tiobe_rating_entry"):
            self.delete_entry(result[2])
            removed += 1
        if entry is None:
            self.logger.info("TiobeRatingAgent: immerse_entry removed %d, created 0", removed)
            return None
        title = entry_title(entry, month)  # до создания узла: ошибка здесь не оставит в памяти пустой записи
        node = create_node(sc_types.NODE_CONST)
        create_edge(sc_types.EDGE_ACCESS_CONST_POS_PERM, ScKeynodes.resolve(ENTRY_CLASS, sc_types.NODE_CONST_CLASS), node)
        self.add_link(node, title, "nrel_main_idtf", "lang_ru")
        self.add_link(node, f"position {entry.position}, {month}, {entry.share}", "nrel_main_idtf", "lang_en")
        self.add_link(node, entry.position, "nrel_rating_position")
        self.add_link(node, entry.share, "nrel_rating_share")
        self.add_link(node, month, "nrel_rating_month")
        self.add_norole(language, node, "nrel_tiobe_rating_entry")
        self.logger.info("TiobeRatingAgent: immerse_entry removed %d, created 1: %s", removed, title)
        return node

    # Процедура 6. Вывод нового знания: класс популярности языка
    def derive_popularity(self, language: ScAddr, entry) -> str:
        """Первая десятка — популярный, места 11-50 — менее популярный, иначе — вне первых 50."""
        status = POPULARITY_CLASSES[popularity(entry)]
        for cls in POPULARITY_CLASSES.values():
            delete_edges(ScKeynodes.resolve(cls, sc_types.NODE_CONST_CLASS), language, sc_types.EDGE_ACCESS_VAR_POS_PERM)
        create_edge(sc_types.EDGE_ACCESS_CONST_POS_PERM, ScKeynodes.resolve(status, sc_types.NODE_CONST_CLASS), language)
        self.logger.info("TiobeRatingAgent: derive_popularity %s (position %s, top %s of %s)",
                         status, entry.position if entry else "none", POPULAR_TOP, RATING_SIZE)
        return status

    # Вспомогательные операции
    def add_link(self, node: ScAddr, value, nrel_idtf: str, lang: str = "") -> None:
        content_type = ScLinkContentType.INT if isinstance(value, int) else ScLinkContentType.STRING
        link = create_link(value, content_type, link_type=sc_types.LINK_CONST)
        if lang:
            create_edge(sc_types.EDGE_ACCESS_CONST_POS_PERM, ScKeynodes.resolve(lang, sc_types.NODE_CONST_CLASS), link)
        self.add_norole(node, link, nrel_idtf)

    def add_norole(self, src: ScAddr, trg: ScAddr, nrel_idtf: str) -> None:
        edge = create_edge(sc_types.EDGE_D_COMMON_CONST, src, trg)
        create_edge(sc_types.EDGE_ACCESS_CONST_POS_PERM, ScKeynodes.resolve(nrel_idtf, sc_types.NODE_CONST_NOROLE), edge)

    def norole_results(self, src: ScAddr, nrel_idtf: str):
        template = ScTemplate()
        template.triple_with_relation(
            src, sc_types.EDGE_D_COMMON_VAR, sc_types.NODE_VAR,
            sc_types.EDGE_ACCESS_VAR_POS_PERM, ScKeynodes.resolve(nrel_idtf, sc_types.NODE_CONST_NOROLE))
        return template_search(template)

    def delete_entry(self, node: ScAddr) -> None:
        """Удалить запись вместе с её ссылками (название, место, доля, месяц)."""
        links = []
        for nrel in ("nrel_main_idtf", "nrel_rating_position", "nrel_rating_share", "nrel_rating_month"):
            template = ScTemplate()
            template.triple_with_relation(
                node, sc_types.EDGE_D_COMMON_VAR, sc_types.LINK_VAR,
                sc_types.EDGE_ACCESS_VAR_POS_PERM, ScKeynodes.resolve(nrel, sc_types.NODE_CONST_NOROLE))
            links += [result[2] for result in template_search(template)]
        delete_elements(*links, node)

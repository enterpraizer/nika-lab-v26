"""Модуль агента рейтинга TIOBE (ЛР4)."""

from sc_kpm import ScModule

from .TiobeRatingAgent import TiobeRatingAgent


class TiobeRatingModule(ScModule):
    def __init__(self):
        super().__init__(TiobeRatingAgent())

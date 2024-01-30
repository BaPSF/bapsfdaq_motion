__all__ = ["IPv4Validator", "QLineEditeSpecialized"]

import logging

from PySide6.QtCore import Signal
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QLineEdit

from bapsf_motion.utils import ipv4_pattern as _ipv4_pattern


class IPv4Validator(QValidator):
    def __init__(self, logger=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pattern = _ipv4_pattern

        log_name = "" if logger is None else f"{logger.name}."
        log_name += "IPv4Validator"
        self._logger = logging.getLogger(log_name)

    def validate(self, arg__1: str, arg__2: int) -> object:
        string = arg__1.replace("_", "")

        match = self._pattern.fullmatch(string)
        if match is None:
            self._logger.warning(f"IP address is invalid, '{string}'.")
            return QValidator.State.Intermediate

        return QValidator.State.Acceptable


class QLineEditeSpecialized(QLineEdit):
    editingFinishedPayload = Signal(object)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.editingFinished.connect(self._send_payload)

    def _send_payload(self):
        self.editingFinishedPayload.emit(self)


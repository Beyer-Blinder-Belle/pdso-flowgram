# -*- coding: utf-8 -*-
"""Collect log records of all PDSO services while a block of code runs."""

# Python standard libraries
import logging


class _ListHandler(logging.Handler):
    def __init__(self, level):
        logging.Handler.__init__(self, level)
        self.records = []

    def emit(self, record):
        try:
            self.records.append(
                {"level": record.levelname.lower(), "message": record.getMessage()}
            )
        except Exception:
            pass


class LogCapture(object):
    """Context manager that collects the log records at or above a level.

    Example Usage:
        with LogCapture() as capture:
            service.run()
        print(capture.records)
    """

    def __init__(self, level=logging.WARNING):
        self._handler = _ListHandler(level)
        self.records = self._handler.records

    def __enter__(self):
        logging.getLogger().addHandler(self._handler)
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        logging.getLogger().removeHandler(self._handler)
        return False

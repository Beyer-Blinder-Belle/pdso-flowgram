# -*- coding: utf-8 -*-
"""Collect log records of all PDSO services while a block of code runs.

The services log with ``script.get_logger()``. Those records do not reach a
handler on the root logger:

- In the newer pyRevit runtime, the logger is a facade whose ``_emit`` sends
  each message straight to the runtime's logging service, not through Python
  logging.
- In older pyRevit, the logger is a ``logging.Logger`` subclass with
  ``propagate = False``.

So LogCapture also wraps the pyRevit logger class for the duration of the
block: ``_emit`` in the newer runtime, ``handle`` in the older one. The
original method is put back on exit.
"""

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


def _pyrevit_logger_class():
    try:
        from pyrevit import script

        return type(script.get_logger())
    except Exception:
        return None


def _plain_function(method):
    """The function of a method (IronPython 2.7 gives unbound methods)."""
    return getattr(method, "__func__", getattr(method, "im_func", method))


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
        self._level = level
        self._restore = []

    def _add(self, level, message, args):
        if level < self._level:
            return
        try:
            text = message % tuple(args) if args else message
        except Exception:
            text = message
        self.records.append(
            {"level": logging.getLevelName(level).lower(), "message": "{}".format(text)}
        )

    def _wrap(self, cls, name, make):
        own = name in vars(cls)
        original = vars(cls)[name] if own else getattr(cls, name)
        setattr(cls, name, make(_plain_function(original)))
        self._restore.append((cls, name, own, original))

    def __enter__(self):
        logging.getLogger().addHandler(self._handler)
        cls = _pyrevit_logger_class()
        if cls is None:
            return self
        capture = self
        if hasattr(cls, "_emit"):
            # Newer pyRevit: the facade bypasses Python logging

            def make(original):
                def _emit(logger, level, message, args=(), *rest, **kwargs):
                    capture._add(level, message, args)
                    return original(logger, level, message, args, *rest, **kwargs)

                return _emit

            self._wrap(cls, "_emit", make)
        elif issubclass(cls, logging.Logger):
            # Older pyRevit: a Logger that does not propagate to the root

            def make(original):
                def handle(logger, record):
                    if not logger.propagate and record.levelno >= capture._level:
                        capture._handler.handle(record)
                    return original(logger, record)

                return handle

            self._wrap(cls, "handle", make)
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        logging.getLogger().removeHandler(self._handler)
        for cls, name, own, original in reversed(self._restore):
            try:
                if own:
                    setattr(cls, name, original)
                else:
                    delattr(cls, name)
            except Exception:
                pass
        self._restore = []
        return False

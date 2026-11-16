# -*- coding: utf-8 -*-
"""Collect log records of all PDSO services while a block of code runs.

The services log with ``script.get_logger()``. Those records do not reach a
handler on the root logger:

- In the newer pyRevit runtime, the logger is a facade whose ``_emit`` sends
  each message straight to the runtime's logging service, not through Python
  logging.
- In older pyRevit, the logger is a ``logging.Logger`` subclass with
  ``propagate = False``.

So while at least one LogCapture is open, the pyRevit logger class is wrapped
once (``_emit`` in the newer runtime, ``handle`` in the older one). The
wrapper gives each record to every open capture. When the last capture
closes, the original method is put back, whatever order captures close in.

The wrapper is on the class, so a capture also gets records that other
scripts log while it is open. The API runs one request at a time, so in
practice those are the records of that request.
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


# The open captures, and how to undo the wrapper: (class, name, own, original)
_open_captures = []
_patch = []


def _install():
    cls = _pyrevit_logger_class()
    if cls is None:
        return
    if hasattr(cls, "_emit"):
        # Newer pyRevit: the facade bypasses Python logging
        name = "_emit"

        def make(original):
            def _emit(logger, level, message, args=(), *rest, **kwargs):
                for capture in list(_open_captures):
                    capture._add(level, message, args)
                return original(logger, level, message, args, *rest, **kwargs)

            return _emit

    elif issubclass(cls, logging.Logger):
        # Older pyRevit: a Logger that does not propagate to the root
        name = "handle"

        def make(original):
            def handle(logger, record):
                if not logger.propagate:
                    for capture in list(_open_captures):
                        if record.levelno >= capture._level:
                            capture._handler.handle(record)
                return original(logger, record)

            return handle

    else:
        return
    own = name in vars(cls)
    original = vars(cls)[name] if own else getattr(cls, name)
    setattr(cls, name, make(_plain_function(original)))
    _patch.append((cls, name, own, original))


def _uninstall():
    while _patch:
        cls, name, own, original = _patch.pop()
        try:
            if own:
                setattr(cls, name, original)
            else:
                delattr(cls, name)
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
        self._level = level

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

    def __enter__(self):
        logging.getLogger().addHandler(self._handler)
        if not _open_captures:
            _install()
        _open_captures.append(self)
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        logging.getLogger().removeHandler(self._handler)
        if self in _open_captures:
            _open_captures.remove(self)
        if not _open_captures:
            _uninstall()
        return False

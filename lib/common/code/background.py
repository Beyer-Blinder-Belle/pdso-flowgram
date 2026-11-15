# -*- coding: utf-8 -*-
"""Helpers for file access that must not block Revit.

Files on a disconnected network drive can make simple calls such as
os.path.isfile() or os.listdir() hang for many seconds while Windows retries
the connection. Run that kind of call through call_with_timeout().

Never call the Revit API from these helpers: they run on a worker thread.
"""

# Python standard libraries
import threading


class TimedOut(Exception):
    """Raised by call_with_timeout() when the call doesn't finish in time."""


def call_with_timeout(function, timeout, *args, **kwargs):
    """Run function(*args, **kwargs) on a worker thread and wait up to `timeout` seconds.

    Returns:
        Whatever the function returns.

    Raises:
        TimedOut: If the function didn't finish in time. The worker thread keeps
            running in the background (it's a daemon thread, so it never keeps
            Revit from closing).
        Exception: Whatever the function raised.
    """
    outcome = {}

    def run():
        try:
            outcome["result"] = function(*args, **kwargs)
        except Exception as e:
            outcome["error"] = e

    worker = threading.Thread(target=run)
    worker.daemon = True
    worker.start()
    worker.join(timeout)

    if worker.is_alive():
        raise TimedOut("Timed out after {} s".format(timeout))
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("result")

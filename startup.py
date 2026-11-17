# -*- coding: utf-8 -*-
"""PDSO HTTP API registration (pyRevit Routes).

pyRevit runs this file once per session. It registers one catch-all route per
HTTP method under the ``pdso`` API name. The real routing lives in
``pdso.code.api.dispatcher`` and is imported on each request, so the API code
can be replaced without a pyRevit reload (see ``POST /pdso/dev/reload``).

The routes server must be enabled: ``pyrevit configs routes enable``.
Base URL: ``http://127.0.0.1:<routes port>/pdso/``.

The PDSO code runs one request at a time. pyRevit Routes gives all requests
that need the Revit API one shared handler object, so two requests that overlap
can get each other's reply. To prevent that, the route functions here take no
Revit API argument. pyRevit then calls them on the HTTP thread of the request.
Each call puts a job in a queue and waits. One external event runs the jobs on
Revit's main thread, one at a time, and gives each job its own reply.

That is not a full fix: in tests, Routes still gave a client the reply of a
different request when requests overlapped. Clients send a request id (_rid)
and check the "rid" of the reply; use one client for each Revit.
"""

import threading
import traceback
from collections import deque

from pyrevit import routes
from pyrevit.api import UI

api = routes.API("pdso")

# Seconds between two checks that the external event is raised
RAISE_INTERVAL = 0.25


class _Job(object):
    """One request that waits for Revit's main thread."""

    def __init__(self, method, path, request):
        self.method = method
        self.path = path
        self.request = request
        self.response = None
        self.done = threading.Event()


class _JobRunner(UI.IExternalEventHandler):
    """Runs the queued jobs on Revit's main thread."""

    def __init__(self):
        self.jobs = deque()
        self.lock = threading.Lock()

    def Execute(self, uiapp):
        while True:
            with self.lock:
                if not self.jobs:
                    return
                job = self.jobs.popleft()
            try:
                from pdso.code.api import dispatcher

                job.response = dispatcher.dispatch(
                    job.method, job.path, job.request, uiapp
                )
            except Exception as error:
                job.response = routes.make_response(
                    data={
                        "ok": False,
                        "error": {
                            "type": type(error).__name__,
                            "message": str(error),
                            "traceback": traceback.format_exc(),
                        },
                    },
                    status=500,
                )
            finally:
                job.done.set()

    def GetName(self):
        return "PDSO HTTP API"


# pyRevit runs this file on Revit's main thread, where Revit permits the
# creation of an external event.
_RUNNER = _JobRunner()
_EVENT = UI.ExternalEvent.Create(_RUNNER)


def _dispatch(method, request, path):
    job = _Job(method, path, request)
    with _RUNNER.lock:
        _RUNNER.jobs.append(job)
    # Raise() does nothing if the event is raised already. Raise it again until
    # the job is done, so that a job that arrives while the runner ends is not
    # left in the queue.
    while not job.done.is_set():
        _EVENT.Raise()
        job.done.wait(RAISE_INTERVAL)
    return job.response


@api.route("/<path>", methods=["GET"])
def pdso_get(request, path):
    return _dispatch("GET", request, path)


@api.route("/<path>", methods=["POST"])
def pdso_post(request, path):
    return _dispatch("POST", request, path)


@api.route("/<path>", methods=["PUT"])
def pdso_put(request, path):
    return _dispatch("PUT", request, path)


@api.route("/<path>", methods=["DELETE"])
def pdso_delete(request, path):
    return _dispatch("DELETE", request, path)

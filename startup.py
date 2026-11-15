# -*- coding: utf-8 -*-
"""PDSO HTTP API registration (pyRevit Routes).

pyRevit runs this file once per session. It registers one catch-all route per
HTTP method under the ``pdso`` API name. The real routing lives in
``pdso.code.api.dispatcher`` and is imported on each request, so the API code
can be replaced without a pyRevit reload (see ``POST /pdso/dev/reload``).

The routes server must be enabled: ``pyrevit configs routes enable``.
Base URL: ``http://127.0.0.1:<routes port>/pdso/``.
"""

from pyrevit import routes

api = routes.API("pdso")


def _dispatch(method, request, uiapp, path):
    from pdso.code.api import dispatcher

    return dispatcher.dispatch(method, path, request, uiapp)


@api.route("/<path>", methods=["GET"])
def pdso_get(request, uiapp, path):
    return _dispatch("GET", request, uiapp, path)


@api.route("/<path>", methods=["POST"])
def pdso_post(request, uiapp, path):
    return _dispatch("POST", request, uiapp, path)


@api.route("/<path>", methods=["PUT"])
def pdso_put(request, uiapp, path):
    return _dispatch("PUT", request, uiapp, path)


@api.route("/<path>", methods=["DELETE"])
def pdso_delete(request, uiapp, path):
    return _dispatch("DELETE", request, uiapp, path)

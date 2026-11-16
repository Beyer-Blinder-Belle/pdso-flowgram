# -*- coding: utf-8 -*-
"""Request dispatcher for the PDSO HTTP API.

``startup.py`` sends every request under ``/pdso/`` to ``dispatch``. A route is
``(method, pattern, handler)``. A pattern segment ``<name>`` or ``<int:name>``
captures one path segment. A handler takes ``(ctx, **path_params)`` and returns
JSON-serializable data.

Response body: ``{"ok": true, "data": ...}`` or
``{"ok": false, "error": {"type", "message", "traceback"}}``.

A client can send the query parameter ``_rid`` (a request id). The response
body then has the same value in ``"rid"``. The client uses it to make sure that
a reply belongs to its request (pyRevit Routes gave a client the reply of a
different request in tests with more than one request at the same time).
"""

import os
import re
import sys
import traceback
import urllib

from pyrevit import routes

from pdso.code.business.errors import PDSOError as ApiError


REQUEST_ID_PARAM = "_rid"


def _request_id(request):
    try:
        return (getattr(request, "query_params", None) or {}).get(REQUEST_ID_PARAM)
    except Exception:
        return None


class Context(object):
    """Request context that the handlers get."""

    def __init__(self, uiapp, request):
        self.uiapp = uiapp
        self.app = uiapp.Application if uiapp else None
        self.uidoc = getattr(uiapp, "ActiveUIDocument", None) if uiapp else None
        self.doc = self.uidoc.Document if self.uidoc else None
        self.request = request
        body = getattr(request, "data", None)
        self.body = body if isinstance(body, dict) else {}
        self.query = dict(getattr(request, "query_params", None) or {})
        # The request id is not a filter or an argument of a handler
        self.query.pop(REQUEST_ID_PARAM, None)

    def require_doc(self):
        """Return the project document of the request, or raise ApiError.

        The field ``document`` (a title or a file path of an open project)
        selects the document. Without it, the active document is used.
        """
        reference = self.arg("document")
        if reference:
            return self._open_document(str(reference))
        if self.doc is None:
            raise ApiError("No document is open in Revit.", 409)
        if self.doc.IsFamilyDocument:
            raise ApiError("The active document is a family. Open a project.", 409)
        return self.doc

    def require_active_doc(self):
        """Return the active document, for a route that uses the Revit window.

        Selection and views act on the active document only. If the request
        names another document (``document``), this raises an ApiError
        instead of mixing the two: ids or views of one document would be
        used in the window of the other.
        """
        doc = self.require_doc()
        if self.doc is None or not doc.Equals(self.doc):
            raise ApiError(
                "This works only on the active document ('{}'), and the request "
                "is for '{}'. Activate that document in Revit, or clear the "
                "target document.".format(
                    self.doc.Title if self.doc is not None else "none", doc.Title
                ),
                409,
            )
        return doc

    def _open_document(self, reference):
        """Find an open project document by title or by file path."""
        wanted = reference.strip().lower()
        titles = []
        for doc in self.app.Documents:
            if doc.IsFamilyDocument or doc.IsLinked:
                continue
            titles.append(doc.Title)
            title = doc.Title.lower()
            names = [title, doc.PathName.lower()]
            if title.endswith(".rvt"):
                names.append(title[:-4])
            if wanted in names or wanted + ".rvt" in names:
                return doc
        raise ApiError(
            "Document '{}' is not open. Open documents: {}".format(
                reference, ", ".join(titles) or "none"
            ),
            404,
        )

    def arg(self, name, default=None, required=False):
        """Read a value from the JSON body, then from the query string."""
        if name in self.body and self.body[name] is not None:
            return self.body[name]
        if name in self.query and self.query[name] is not None:
            return self.query[name]
        if required:
            raise ApiError("Missing required field: '{}'".format(name))
        return default


def dev_mode_enabled():
    """Dev routes are on only when the flag file exists."""
    flag = os.path.join(os.environ.get("APPDATA", ""), "pyRevit", "pdso_api_dev.flag")
    return os.path.exists(flag)


def _compile(pattern):
    parts = []
    for segment in pattern.strip("/").split("/"):
        match = re.match(r"^<(?:(int):)?(\w+)>$", segment)
        if match:
            kind, name = match.groups()
            parts.append("(?P<{}>{})".format(name, r"-?\d+" if kind else r"[^/]+"))
        else:
            parts.append(re.escape(segment))
    return re.compile("^" + "/".join(parts) + "/?$")


def _convert(pattern, params):
    for segment in pattern.strip("/").split("/"):
        match = re.match(r"^<int:(\w+)>$", segment)
        if match:
            params[match.group(1)] = int(params[match.group(1)])
    return params


def _find(method, path):
    from pdso.code.api import handlers
    from pdso.code.api import catalogue_handlers  # noqa: F401 (adds its routes)

    path = (path or "").strip("/")
    allowed = []
    for route_method, pattern, func in handlers.ROUTES:
        match = _compile(pattern).match(path)
        if not match:
            continue
        if route_method == method:
            params = dict(
                (name, urllib.unquote(value))
                for name, value in match.groupdict().items()
            )
            return func, _convert(pattern, params)
        allowed.append(route_method)
    if allowed:
        raise ApiError(
            "Method {} is not allowed for /{}. Use: {}".format(
                method, path, ", ".join(sorted(set(allowed)))
            ),
            405,
        )
    raise ApiError("No route: {} /{}. See GET /pdso/routes.".format(method, path), 404)


def _error(status, exc_type, message, tb=None, rid=None):
    data = {
        "ok": False,
        "error": {"type": exc_type, "message": message, "traceback": tb},
    }
    if rid is not None:
        data["rid"] = rid
    return routes.make_response(data=data, status=status)


def reload_modules():
    """Drop the cached ``pdso`` modules. The next request imports new code."""
    names = [name for name in sys.modules if name == "pdso" or name.startswith("pdso.")]
    for name in names:
        del sys.modules[name]
    return names


def dispatch(method, path, request, uiapp):
    """Find the handler for the request, call it, and wrap the result."""
    rid = _request_id(request)
    try:
        func, params = _find(method, path)
        ctx = Context(uiapp, request)
        data = func(ctx, **params)
        body = {"ok": True, "data": data}
        if rid is not None:
            body["rid"] = rid
        return routes.make_response(data=body)
    except ApiError as api_error:
        return _error(api_error.status, "ApiError", str(api_error), rid=rid)
    except Exception as error:
        return _error(
            500, type(error).__name__, str(error), traceback.format_exc(), rid=rid
        )

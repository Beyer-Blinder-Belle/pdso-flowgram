# -*- coding: utf-8 -*-
"""Request dispatcher for the PDSO HTTP API.

``startup.py`` sends every request under ``/pdso/`` to ``dispatch``. A route is
``(method, pattern, handler)``. A pattern segment ``<name>`` or ``<int:name>``
captures one path segment. A handler takes ``(ctx, **path_params)`` and returns
JSON-serializable data.

Response body: ``{"ok": true, "data": ...}`` or
``{"ok": false, "error": {"type", "message", "traceback"}}``.
"""

import os
import re
import sys
import traceback
import urllib

from pyrevit import routes

from pdso.code.business.errors import PDSOError as ApiError


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

    def require_doc(self):
        """Return the active project document, or raise ApiError."""
        if self.doc is None:
            raise ApiError("No document is open in Revit.", 409)
        if self.doc.IsFamilyDocument:
            raise ApiError("The active document is a family. Open a project.", 409)
        return self.doc

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


def _error(status, exc_type, message, tb=None):
    return routes.make_response(
        data={
            "ok": False,
            "error": {"type": exc_type, "message": message, "traceback": tb},
        },
        status=status,
    )


def reload_modules():
    """Drop the cached ``pdso`` modules. The next request imports new code."""
    names = [name for name in sys.modules if name == "pdso" or name.startswith("pdso.")]
    for name in names:
        del sys.modules[name]
    return names


def dispatch(method, path, request, uiapp):
    """Find the handler for the request, call it, and wrap the result."""
    try:
        func, params = _find(method, path)
        ctx = Context(uiapp, request)
        data = func(ctx, **params)
        return routes.make_response(data={"ok": True, "data": data})
    except ApiError as api_error:
        return _error(api_error.status, "ApiError", str(api_error))
    except Exception as error:
        return _error(500, type(error).__name__, str(error), traceback.format_exc())

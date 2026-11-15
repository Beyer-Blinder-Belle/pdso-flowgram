# -*- coding: utf-8 -*-
"""Routes of the PDSO HTTP API for open documents and the Unit Layout Catalogue.

The dispatcher imports this module. The module adds its routes to the route
table of ``handlers``, so that ``GET /pdso/routes`` lists them.

A request can name the document that it works on with the field ``document``
(a title or a file path of an open project). Without it, a request works on the
active document.
"""

import os

from pyrevit import DB

from pdso.code.api import handlers
from pdso.code.api.dispatcher import ApiError
from pdso.code.catalogue import catalogue_api_service as catalogue


def route(method, pattern):
    """Register a handler in the route table of ``handlers`` (one time)."""

    def wrapper(func):
        for index, (known_method, known_pattern, _) in enumerate(handlers.ROUTES):
            if known_method == method and known_pattern == pattern:
                handlers.ROUTES[index] = (method, pattern, func)
                return func
        # Routes with a path parameter must come after the fixed routes
        handlers.ROUTES.append((method, pattern, func))
        return func

    return wrapper


def _flag(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# =============================================================================
# Open documents
# =============================================================================


def _document_info(doc, active_doc):
    return {
        "title": doc.Title,
        "path": doc.PathName,
        "is_active": active_doc is not None and doc.Equals(active_doc),
        "is_modified": doc.IsModified,
        "is_workshared": doc.IsWorkshared,
    }


@route("GET", "documents")
def list_documents(ctx):
    """List the project documents that are open in Revit."""
    return [
        _document_info(doc, ctx.doc)
        for doc in ctx.app.Documents
        if not doc.IsFamilyDocument and not doc.IsLinked
    ]


@route("POST", "documents/open")
def open_document_in_background(ctx):
    """Open a project file but do not show it. Body: path, detach?"""
    path = ctx.arg("path", required=True)
    if not os.path.exists(path):
        raise ApiError("File not found: {}".format(path), 404)
    for doc in ctx.app.Documents:
        if os.path.normcase(doc.PathName) == os.path.normcase(path):
            return _document_info(doc, ctx.doc)
    options = DB.OpenOptions()
    if _flag(ctx.arg("detach")):
        options.DetachFromCentralOption = DB.DetachFromCentralOption.DetachAndPreserveWorksets
    model_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(path)
    doc = ctx.app.OpenDocumentFile(model_path, options)
    return _document_info(doc, ctx.doc)


@route("POST", "documents/save")
def save_document(ctx):
    """Save a document to its file. Body: document"""
    doc = ctx.require_doc()
    if not doc.PathName:
        raise ApiError("The document has no file path.", 409)
    doc.Save()
    return _document_info(doc, ctx.doc)


@route("POST", "documents/close")
def close_document(ctx):
    """Close a document that is not the active document. Body: document, save?"""
    doc = ctx.require_doc()
    if ctx.doc is not None and doc.Equals(ctx.doc):
        raise ApiError(
            "The API cannot close the active document. Use POST document/close.", 409
        )
    title = doc.Title
    doc.Close(_flag(ctx.arg("save")))
    return {"closed": title}


# =============================================================================
# Unit Layout Catalogue
# =============================================================================


@route("GET", "catalogue/layouts")
def list_layouts(ctx):
    """List the catalogue layouts. Query: program?, project_type?"""
    return catalogue.list_layouts(ctx.arg("program"), ctx.arg("project_type"))


@route("POST", "catalogue/harvest")
def harvest_layout(ctx):
    """Save the layout of a Room or Area. Body: room, name?, program?, project_type?, notes?"""
    return catalogue.harvest_layout(
        ctx.require_doc(),
        ctx.arg("room", required=True),
        name=ctx.arg("name"),
        program=ctx.arg("program"),
        project_type=ctx.arg("project_type", "Other"),
        notes=ctx.arg("notes", ""),
        folder=ctx.arg("folder"),
    )


@route("POST", "catalogue/recommend")
def recommend_layouts(ctx):
    """Rank the layouts for a target Room or Area. Body: room, program?, limit?"""
    return catalogue.recommend_layouts(
        ctx.require_doc(),
        ctx.arg("room", required=True),
        program=ctx.arg("program"),
        limit=ctx.arg("limit", 10),
    )


@route("POST", "catalogue/place")
def place_layout(ctx):
    """Place a layout into a target Room or Area. Body: room, layout_id"""
    return catalogue.place_layout(
        ctx.require_doc(),
        ctx.arg("room", required=True),
        ctx.arg("layout_id", required=True),
    )


@route("POST", "catalogue/similar-rooms")
def find_similar_rooms(ctx):
    """Find the Rooms that are similar to a layout. Body: layout_id, kind?, level?, limit?"""
    return catalogue.find_similar_rooms(
        ctx.require_doc(),
        ctx.arg("layout_id", required=True),
        kind=ctx.arg("kind", "rooms"),
        level=ctx.arg("level"),
        limit=ctx.arg("limit", 20),
    )


@route("GET", "catalogue/preview")
def preview_layout_in_room(ctx):
    """Data for a picture of a layout in a Room or Area (read-only). Query: room, layout_id"""
    return catalogue.preview_layout_in_room(
        ctx.require_doc(),
        ctx.arg("room", required=True),
        ctx.arg("layout_id", required=True),
    )


@route("GET", "catalogue/item")
def describe_item(ctx):
    """Describe one family instance (read-only): position, flips, plan box, instance parameters. Query: id"""
    return catalogue.describe_item(ctx.require_doc(), ctx.arg("id", required=True))


@route("GET", "catalogue/room-items")
def list_room_items(ctx):
    """List the free-standing families in a Room or Area. Query: room"""
    return catalogue.list_room_items(ctx.require_doc(), ctx.arg("room", required=True))


@route("DELETE", "catalogue/room-items")
def clear_room_items(ctx):
    """Delete the free-standing families in a Room or Area. Query: room"""
    return catalogue.clear_room_items(ctx.require_doc(), ctx.arg("room", required=True))


@route("GET", "catalogue/layouts/<layout_id>")
def get_layout(ctx, layout_id):
    """Get one layout: its JSON entry and its SVG sketch."""
    return catalogue.get_layout(layout_id)


@route("DELETE", "catalogue/layouts/<layout_id>")
def delete_layout(ctx, layout_id):
    """Delete one layout file (not a sample layout)."""
    return catalogue.delete_layout(layout_id)

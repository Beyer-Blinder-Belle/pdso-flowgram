# -*- coding: utf-8 -*-
"""Routes of the PDSO HTTP API for the Unit Layout Catalogue.

The dispatcher imports this module. The module adds its routes to the route
table of ``handlers``, so that ``GET /pdso/routes`` lists them. The routes for
open documents are in ``handlers``.

A request can name the document that it works on with the field ``document``
(a title or a file path of an open project). Without it, a request works on the
active document.
"""

from pdso.code.api import handlers
from pdso.code.api.handlers import _flag
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
        # No "folder" from the request: the API has no authentication, so a
        # caller must not choose where files are written. Harvests go to the
        # catalogue folder set in Revit (Shift+Click Harvest Layout).
    )


@route("POST", "catalogue/recommend")
def recommend_layouts(ctx):
    """Rank the layouts for a target Room or Area (read-only). Body: room, program?, limit?

    POST although it changes nothing: a body reaches the handler on every
    pyRevit, query parameters don't (pyRevit 6.1 drops them).
    """
    return catalogue.recommend_layouts(
        ctx.require_doc(),
        ctx.arg("room", required=True),
        program=ctx.arg("program"),
        limit=ctx.arg("limit", 10),
    )


@route("POST", "catalogue/place")
def place_layout(ctx):
    """Place a layout into a target Room or Area. Body: room, layout_id, clear_first?"""
    return catalogue.place_layout(
        ctx.require_doc(),
        ctx.arg("room", required=True),
        ctx.arg("layout_id", required=True),
        _flag(ctx.arg("clear_first")),
    )


@route("POST", "catalogue/similar-rooms")
def find_similar_rooms(ctx):
    """Find the Rooms that are similar to a layout (read-only). Body: layout_id, kind?, level?, limit?

    POST for the same reason as catalogue/recommend.
    """
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

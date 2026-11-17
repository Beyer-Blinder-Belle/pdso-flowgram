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
from pdso.code.api.dispatcher import ApiError
from pdso.code.api.handlers import _flag
from pdso.code.business import model_service
from pdso.code.business.revit_helpers import id_value
from pdso.code.catalogue import catalogue_api_service as catalogue
from pdso.code.catalogue import revit_layout_service as layout_service


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
#
# A unit is an Area with a PDSO data silo inside. "unit" is its Area id, its
# data silo's id or its Program UID. API 1.9 renamed the Room words: "room"
# is still read as "unit", catalogue/similar-rooms and catalogue/room-items
# still work, and replies still carry "room", "rooms" and "rooms_checked"
# beside the new keys. The old names go in the next API version.

_OLD_KEYS = (("unit", "room"), ("units", "rooms"), ("units_checked", "rooms_checked"))


def _unit_arg(ctx):
    """The "unit" argument ("room" before API 1.9)."""
    value = ctx.arg("unit")
    if value is None:
        value = ctx.arg("room")
    if value is None:
        raise ApiError("Give the unit: its Area id, its data silo's id or its Program UID.")
    return value


def _with_old_keys(result):
    """Copy the new reply keys to the names used before API 1.9."""
    if isinstance(result, dict):
        for new, old in _OLD_KEYS:
            if new in result and old not in result:
                result[old] = result[new]
    return result


@route("GET", "catalogue/layouts")
def list_layouts(ctx):
    """List the catalogue layouts. Query: program?, project_type?"""
    return catalogue.list_layouts(ctx.arg("program"), ctx.arg("project_type"))


@route("POST", "catalogue/harvest")
def harvest_layout(ctx):
    """Save the layout of one unit. Body: unit, name?, program?, project_type?, notes?"""
    return catalogue.harvest_layout(
        ctx.require_doc(),
        _unit_arg(ctx),
        name=ctx.arg("name"),
        program=ctx.arg("program"),
        project_type=ctx.arg("project_type", "Other"),
        notes=ctx.arg("notes", ""),
        # No "folder" from the request: the API has no authentication, so a
        # caller must not choose where files are written. Harvests go to the
        # catalogue folder set in Revit (Shift+Click Harvest Layout).
    )


@route("POST", "catalogue/harvest-units")
def harvest_units(ctx):
    """Harvest units as the Harvest All Units button does. Body: view?, areas?, project_type?

    Without "areas", every Area of the area plan "view" (default: the active
    view) that has a PDSO data silo inside. Plan images come from "view".
    Running again replaces the earlier harvest of the same units.
    """
    doc = ctx.require_doc()
    view = model_service.find_view(doc, ctx.arg("view"))
    references = ctx.arg("areas")
    without = []
    if references:
        if not isinstance(references, list):
            references = [references]
        elements = [catalogue.get_unit(doc, ref) for ref in references]
    else:
        elements, without = catalogue.units_in_view(doc, view)
    if not elements:
        raise ApiError(
            "No Area with a PDSO data silo inside is in '{}'. {} placed Area(s) "
            "have no silo.".format(view.Name, len(without)),
            404,
        )
    report = catalogue.public_report(
        catalogue.harvest_units(
            doc, elements, ctx.arg("project_type", "Other"), view=view
        )
    )
    report["view"] = view.Name
    report["without_silo"] = [
        {"id": id_value(area.Id), "name": layout_service.get_name(area), "area": area.Area}
        for area in without
    ]
    return report


@route("POST", "catalogue/recommend")
def recommend_layouts(ctx):
    """Rank the layouts for a target unit (read-only). Body: unit, program?, limit?

    POST although it changes nothing: a body reaches the handler on every
    pyRevit, query parameters don't (pyRevit 6.1 drops them).
    """
    return catalogue.recommend_layouts(
        ctx.require_doc(),
        _unit_arg(ctx),
        program=ctx.arg("program"),
        limit=ctx.arg("limit", 10),
    )


@route("POST", "catalogue/place")
def place_layout(ctx):
    """Place a layout into a target unit. Body: unit, layout_id, clear_first?"""
    return catalogue.place_layout(
        ctx.require_doc(),
        _unit_arg(ctx),
        ctx.arg("layout_id", required=True),
        _flag(ctx.arg("clear_first")),
    )


@route("POST", "catalogue/similar-units")
@route("POST", "catalogue/similar-rooms")
def find_similar_units(ctx):
    """Find the units that are similar to a layout (read-only). Body: layout_id, scheme?, level?, limit?

    POST for the same reason as catalogue/recommend. similar-rooms is the
    name before API 1.9; its "kind" must be "areas" if given.
    """
    if ctx.arg("kind") not in (None, "areas"):
        raise ApiError("PDSO units are Areas; Revit Rooms aren't searched.")
    return _with_old_keys(
        catalogue.find_similar_units(
            ctx.require_doc(),
            ctx.arg("layout_id", required=True),
            scheme=ctx.arg("scheme"),
            level=ctx.arg("level"),
            limit=ctx.arg("limit", 20),
        )
    )


@route("GET", "catalogue/preview")
def preview_layout_in_unit(ctx):
    """Data for a picture of a layout in a unit (read-only). Query: unit, layout_id"""
    return _with_old_keys(
        catalogue.preview_layout_in_unit(
            ctx.require_doc(), _unit_arg(ctx), ctx.arg("layout_id", required=True)
        )
    )


@route("GET", "catalogue/item")
def describe_item(ctx):
    """Describe one family instance (read-only): position, flips, plan box, instance parameters. Query: id"""
    return catalogue.describe_item(ctx.require_doc(), ctx.arg("id", required=True))


@route("GET", "catalogue/unit-items")
@route("GET", "catalogue/room-items")
def list_unit_items(ctx):
    """List the free-standing families in a unit. Query: unit (room-items: before API 1.9)"""
    return _with_old_keys(catalogue.list_unit_items(ctx.require_doc(), _unit_arg(ctx)))


@route("DELETE", "catalogue/unit-items")
@route("DELETE", "catalogue/room-items")
def clear_unit_items(ctx):
    """Delete the free-standing families in a unit. Query: unit (room-items: before API 1.9)"""
    return _with_old_keys(catalogue.clear_unit_items(ctx.require_doc(), _unit_arg(ctx)))


@route("GET", "catalogue/layouts/<layout_id>")
def get_layout(ctx, layout_id):
    """Get one layout: its JSON entry and its SVG sketch."""
    return catalogue.get_layout(layout_id)


@route("DELETE", "catalogue/layouts/<layout_id>")
def delete_layout(ctx, layout_id):
    """Delete one layout file (not a sample layout)."""
    return catalogue.delete_layout(layout_id)

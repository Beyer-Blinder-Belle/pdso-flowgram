# -*- coding: utf-8 -*-
"""PDSO Model Service - document, level and view data for the HTTP API."""

# Python standard libraries
import os
from contextlib import contextmanager

# 3rd Party Modules
from pyrevit import DB

# Custom Modules
from .errors import PDSOError
from .revit_helpers import (
    Transaction,
    all_levels,
    get_element,
    id_value,
    level_info,
    to_number,
)


def document_info(doc):
    """Describe the document."""
    return {
        "title": doc.Title,
        "path": doc.PathName,
        "is_modified": doc.IsModified,
        "is_workshared": doc.IsWorkshared,
        "is_family_document": doc.IsFamilyDocument,
        "active_view": view_info(doc.ActiveView) if doc.ActiveView else None,
        "levels": [level_info(level) for level in all_levels(doc)],
    }


def create_level(doc, name, elevation):
    """Create a Level at an elevation (feet)."""
    if any(level.Name == name for level in all_levels(doc)):
        raise PDSOError("Level '{}' is in the model already.".format(name), 409)
    elevation = to_number(elevation, "elevation")
    with Transaction(doc, "PDSO - Create Level"):
        level = DB.Level.Create(doc, elevation)
        level.Name = str(name)
    return level_info(level)


def view_info(view):
    """Describe a view as a dict."""
    info = {
        "id": id_value(view.Id),
        "name": view.Name,
        "type": str(view.ViewType),
    }
    if isinstance(view, DB.ViewPlan):
        info["level"] = view.GenLevel.Name if view.GenLevel else None
        scheme = view.AreaScheme if view.ViewType == DB.ViewType.AreaPlan else None
        info["area_scheme"] = scheme.Name if scheme else None
    return info


def list_views(doc, view_type=None):
    """List the views that can be shown or exported (no templates)."""
    views = []
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
        if view.IsTemplate or not view.CanBePrinted:
            continue
        if view_type and str(view.ViewType).lower() != str(view_type).lower():
            continue
        views.append(view_info(view))
    return sorted(views, key=lambda item: (item["type"], item["name"]))


def find_view(doc, reference, view_type=None):
    """Find a view by element id (int) or by name.

    view_type (str): Revit view type (for example FloorPlan). It selects one
        view when two views of different types have the same name (optional)
    """
    if reference is None:
        if doc.ActiveView is None:
            raise PDSOError("Give a view (name or id).")
        return doc.ActiveView
    text = str(reference)
    if text.lstrip("-").isdigit():
        return get_element(doc, int(text), DB.View, "View")
    matches = [
        view
        for view in DB.FilteredElementCollector(doc).OfClass(DB.View)
        if not view.IsTemplate and view.Name == text
    ]
    if view_type:
        matches = [
            view
            for view in matches
            if str(view.ViewType).lower() == str(view_type).lower()
        ]
    if not matches:
        raise PDSOError("View '{}' not found.".format(reference), 404)
    if len(matches) > 1:
        raise PDSOError(
            "{} views have the name '{}'. Give the view type, or use the view "
            "id: {}".format(
                len(matches),
                reference,
                ", ".join(
                    "{} ({})".format(id_value(view.Id), view.ViewType)
                    for view in matches
                ),
            ),
            409,
        )
    return matches[0]


def activate_view(uidoc, view, zoom_box=None):
    """Show a view in the Revit window.

    zoom_box: [x_min, y_min, x_max, y_max] in feet (plan views, optional)
    """
    try:
        uidoc.ActiveView = view
    except Exception:
        uidoc.RequestViewChange(view)
    info = view_info(view)
    if zoom_box:
        try:
            x_min, y_min, x_max, y_max = [float(value) for value in zoom_box]
        except (TypeError, ValueError):
            raise PDSOError("'zoom_box' must be [x_min, y_min, x_max, y_max] in feet.")
        info["zoomed"] = False
        for ui_view in uidoc.GetOpenUIViews():
            if ui_view.ViewId == view.Id:
                ui_view.ZoomAndCenterRectangle(
                    DB.XYZ(x_min, y_min, 0), DB.XYZ(x_max, y_max, 0)
                )
                info["zoomed"] = True
    return info


def new_project(uiapp, path, template=None, overwrite=False):
    """Create a project file from a template, then open it."""
    app = uiapp.Application
    path = os.path.abspath(path)
    if os.path.exists(path) and not overwrite:
        raise PDSOError("File is there already: {}".format(path), 409)
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    template = template or app.DefaultProjectTemplate
    if not template or not os.path.exists(template):
        raise PDSOError("Project template not found: {}".format(template), 404)
    new_doc = app.NewProjectDocument(template)
    options = DB.SaveAsOptions()
    options.OverwriteExistingFile = True
    new_doc.SaveAs(path, options)
    new_doc.Close(False)
    return open_project(uiapp, path)


# Dialogs that Revit shows while a project opens, and the answer that lets the
# open continue. A dialog that waits for a click blocks the API request.
# 1002 is TaskDialogResult.CommandLink2.
OPEN_DIALOG_ANSWERS = {
    # "Ignore and continue opening the project"
    "TaskDialog_Unresolved_References": 1002,
}


@contextmanager
def _answering_open_dialogs(uiapp):
    """Answer the dialogs of OPEN_DIALOG_ANSWERS while a project opens.

    Yields:
        tuple: (answered, seen) lists of dialog ids, filled as dialogs show
    """
    answered = []
    seen = []

    def on_dialog(sender, args):
        dialog_id = getattr(args, "DialogId", None) or ""
        if dialog_id in OPEN_DIALOG_ANSWERS:
            try:
                args.OverrideResult(OPEN_DIALOG_ANSWERS[dialog_id])
                answered.append(dialog_id)
                return
            except Exception:
                pass
        seen.append(dialog_id or type(args).__name__)

    uiapp.DialogBoxShowing += on_dialog
    try:
        yield answered, seen
    finally:
        uiapp.DialogBoxShowing -= on_dialog


def open_project(uiapp, path):
    """Open a project file and make it the active document.

    The dialogs of OPEN_DIALOG_ANSWERS get their answer automatically. The
    result lists them in "dialogs_answered", and all other dialogs that showed
    in "dialogs_seen".
    """
    if not os.path.exists(path):
        raise PDSOError("File not found: {}".format(path), 404)
    with _answering_open_dialogs(uiapp) as (answered, seen):
        info = _open_project(uiapp, path)
    info["dialogs_answered"] = answered
    info["dialogs_seen"] = seen
    return info


# =============================================================================
# Open documents (the active one and the ones open in the background)
# =============================================================================


def open_document_info(doc, active_doc):
    """Describe an open project document in a few fields."""
    return {
        "title": doc.Title,
        "path": doc.PathName,
        "is_active": active_doc is not None and doc.Equals(active_doc),
        "is_modified": doc.IsModified,
        "is_workshared": doc.IsWorkshared,
    }


def list_documents(app, active_doc):
    """The project documents that are open in Revit."""
    return [
        open_document_info(doc, active_doc)
        for doc in app.Documents
        if not doc.IsFamilyDocument and not doc.IsLinked
    ]


def open_in_background(uiapp, path, detach=False):
    """Open a project file without showing it. An open file is not opened again.

    Dialogs are answered as open_project answers them.
    """
    if not os.path.exists(path):
        raise PDSOError("File not found: {}".format(path), 404)
    app = uiapp.Application
    active = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
    for doc in app.Documents:
        if os.path.normcase(doc.PathName) == os.path.normcase(path):
            return open_document_info(doc, active)
    options = DB.OpenOptions()
    if detach:
        options.DetachFromCentralOption = (
            DB.DetachFromCentralOption.DetachAndPreserveWorksets
        )
    model_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(path)
    with _answering_open_dialogs(uiapp) as (answered, seen):
        doc = app.OpenDocumentFile(model_path, options)
    info = open_document_info(doc, active)
    info["dialogs_answered"] = answered
    info["dialogs_seen"] = seen
    return info


def find_open_document(app, title):
    """An open project document by title, or a 404 PDSOError."""
    titles = []
    for doc in app.Documents:
        if doc.IsLinked or doc.IsFamilyDocument:
            continue
        titles.append(doc.Title)
        if doc.Title == title:
            return doc
    raise PDSOError(
        "Document '{}' is not open. Open documents: {}".format(
            title, ", ".join(titles)
        ),
        404,
    )


def close_document(doc, active_doc, save=False):
    """Close an open document that is not the active one."""
    if active_doc is not None and doc.Equals(active_doc):
        raise PDSOError(
            "'{}' is the active document. Open a different project first.".format(
                doc.Title
            ),
            409,
        )
    title = doc.Title
    doc.Close(bool(save))
    return {"closed": title, "saved": bool(save)}


def _open_project(uiapp, path):
    workshared = False
    try:
        workshared = DB.BasicFileInfo.Extract(path).IsWorkshared
    except Exception:
        workshared = False
    if workshared:
        # A copy of a central model: open it detached, so that no dialog shows
        options = DB.OpenOptions()
        options.DetachFromCentralOption = (
            DB.DetachFromCentralOption.DetachAndPreserveWorksets
        )
        model_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(path)
        uidoc = uiapp.OpenAndActivateDocument(model_path, options, False)
    else:
        uidoc = uiapp.OpenAndActivateDocument(path)
    info = document_info(uidoc.Document)
    info["opened_detached"] = workshared
    return info


def save_project(doc):
    """Save the document to its file."""
    if not doc.PathName:
        raise PDSOError("The document has no file path. Use new_project first.", 409)
    doc.Save()
    return document_info(doc)



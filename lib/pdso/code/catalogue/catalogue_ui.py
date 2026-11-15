# -*- coding: utf-8 -*-
"""Small UI helpers shared by the Harvest and Place buttons."""

from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from pyrevit import forms, revit

from pdso.code.catalogue.revit_layout_service import is_spatial_element


class _SpatialElementFilter(ISelectionFilter):
    def AllowElement(self, element):
        return is_spatial_element(element)

    def AllowReference(self, reference, point):
        return False


def pick_spatial_element(prompt):
    """Use the one selected Room or Area, or ask the user to pick one.

    Returns:
        Room or Area, or None if the user cancelled.
    """
    selected = [
        revit.doc.GetElement(element_id)
        for element_id in revit.uidoc.Selection.GetElementIds()
    ]
    selected = [element for element in selected if is_spatial_element(element)]
    if len(selected) == 1:
        return selected[0]

    try:
        reference = revit.uidoc.Selection.PickObject(
            ObjectType.Element, _SpatialElementFilter(), prompt
        )
    except OperationCanceledException:
        return None
    return revit.doc.GetElement(reference.ElementId)


PROJECT_TYPES = ["Multifamily", "K-12", "Higher Education", "Office", "Healthcare", "Other"]


def ask_project_type():
    return forms.CommandSwitchWindow.show(
        PROJECT_TYPES, message="Project type this layout comes from:"
    )

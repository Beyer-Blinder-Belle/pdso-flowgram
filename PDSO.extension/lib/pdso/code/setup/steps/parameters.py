# -*- coding: utf-8 -*-
"""Model Setup step: add the PDSO shared parameters to the model.

Every parameter in parameters.json becomes a project parameter bound to the
configured categories, in one run, with no shared parameter file to browse to.

How:
    1. Write a temporary shared parameter file from parameters.json (the same
       builder that makes the shipped "PDSO Shared Parameters.txt"), so the
       GUIDs always match what the PDSO tools look up.
    2. Point Revit at it just long enough to read the definitions, then put the
       user's own shared parameter file setting back.
    3. Bind each definition. A parameter that is already a project parameter is
       left alone, or has missing categories added. A parameter that only exists
       because a loaded family uses it (the Data Silo family does) is bound the
       same way as a new one: Revit matches it by GUID.

Each parameter gets its own transaction, so one failure doesn't undo the rest.
Keys read from parameters.json: categories, binding ("instance" or "type"),
group (e.g. "PG_DATA"), shared_parameter_group, parameters.
"""

# Python standard libraries
import os
import tempfile

# System Modules
from System import Guid

# 3rd Party Modules
from pyrevit import DB

# Custom Modules
from pdso.code.setup import revit_utils, status
from pdso.code.setup.shared_parameter_file import (
    DEFAULT_GROUP_NAME,
    write_shared_parameter_file,
)


def run(ctx, data):
    doc = ctx.doc
    app = doc.Application

    categories = []
    for category_name in data.get("categories", []):
        category = revit_utils.resolve_category(doc, category_name)
        if category is None:
            ctx.add(category_name, status.FAILED, "Category is not in this model")
        elif not category.AllowsBoundParameters:
            ctx.add(category_name, status.FAILED, "Category can't take project parameters")
        else:
            categories.append(category)
    if not categories:
        ctx.add("Categories", status.FAILED, "No category to bind the parameters to")
        return

    is_type_binding = data.get("binding", "instance").lower() == "type"
    group_type = _parameter_group(data.get("group", "PG_DATA"))
    group_name = data.get("shared_parameter_group", DEFAULT_GROUP_NAME)

    definitions = _read_definitions(app, data, group_name)
    bound = _bound_parameter_categories(doc)

    for spec in data.get("parameters", []):
        name = spec.get("name", "")
        guid = Guid(spec["guid"])
        definition = definitions.get(name)
        if definition is None or definition.GUID != guid:
            ctx.add(name, status.FAILED, "Not found in the generated shared parameter file")
            continue

        existing = DB.SharedParameterElement.Lookup(doc, guid)
        bound_categories = bound.get(existing.Id) if existing is not None else None

        if bound_categories is not None:
            missing = [c for c in categories if c.Id not in bound_categories]
            if not missing:
                ctx.add(name, status.SKIPPED, "Already a project parameter")
                continue
            # Keep the categories it already has, add the missing ones
            category_set = _category_set(
                app, categories + [_category_by_id(doc, i) for i in bound_categories]
            )
            row_status = status.UPDATED
            message = "Added categories: {}".format(", ".join(c.Name for c in missing))
        else:
            category_set = _category_set(app, categories)
            row_status = status.CREATED
            message = (
                "Bound to {}".format(", ".join(c.Name for c in categories))
                if existing is None
                else "Was only in loaded families; now a project parameter"
            )

        binding = (
            app.Create.NewTypeBinding(category_set)
            if is_type_binding
            else app.Create.NewInstanceBinding(category_set)
        )

        try:
            with ctx.transaction("PDSO - Bind {}".format(name)):
                if bound_categories is not None:
                    ok = _rebind(doc, definition, binding, group_type)
                else:
                    ok = _bind(doc, definition, binding, group_type)
                if not ok:
                    raise RuntimeError("Revit refused the binding")
        except Exception as error:
            ctx.add(name, status.FAILED, str(error))
            continue

        ctx.add(name, row_status, message)


def _read_definitions(app, data, group_name):
    """{name: ExternalDefinition} from a temporary shared parameter file.

    The user's shared parameter file setting is restored afterwards, even on error.
    """
    path = os.path.join(tempfile.gettempdir(), "PDSO Model Setup - Shared Parameters.txt")
    write_shared_parameter_file(data, path)

    original = app.SharedParametersFilename
    try:
        app.SharedParametersFilename = path
        definition_file = app.OpenSharedParameterFile()
        if definition_file is None:
            raise RuntimeError("Revit could not open the generated shared parameter file")
        group = definition_file.Groups.get_Item(group_name)
        if group is None:
            raise RuntimeError("Group '{}' missing from the generated file".format(group_name))
        return dict((d.Name, d) for d in group.Definitions)
    finally:
        app.SharedParametersFilename = original or ""


def _parameter_group(name):
    """parameters.json group ("PG_DATA") -> GroupTypeId (Revit 2022+), or None."""
    group_type_id = getattr(DB, "GroupTypeId", None)
    if group_type_id is None or not name:
        return None
    words = name.upper().replace("PG_", "", 1).split("_")
    attribute = "".join(word.capitalize() for word in words)  # DATA -> Data
    return getattr(group_type_id, attribute, None)


def _bind(doc, definition, binding, group_type):
    if group_type is not None:
        return doc.ParameterBindings.Insert(definition, binding, group_type)
    return doc.ParameterBindings.Insert(definition, binding)


def _rebind(doc, definition, binding, group_type):
    if group_type is not None:
        return doc.ParameterBindings.ReInsert(definition, binding, group_type)
    return doc.ParameterBindings.ReInsert(definition, binding)


def _category_set(app, categories):
    category_set = app.Create.NewCategorySet()
    seen = set()
    for category in categories:
        if category is not None and category.Id not in seen:
            category_set.Insert(category)
            seen.add(category.Id)
    return category_set


def _category_by_id(doc, category_id):
    for category in doc.Settings.Categories:
        if category.Id == category_id:
            return category
    return None


def _bound_parameter_categories(doc):
    """{parameter ElementId: set of category ElementIds it's bound to}."""
    bound = {}
    iterator = doc.ParameterBindings.ForwardIterator()
    while iterator.MoveNext():
        definition = iterator.Key
        if isinstance(definition, DB.InternalDefinition):
            bound[definition.Id] = set(c.Id for c in iterator.Current.Categories)
    return bound

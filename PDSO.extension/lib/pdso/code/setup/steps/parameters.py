# -*- coding: utf-8 -*-
"""Model Setup step: add the PDSO shared parameters to the model.

PLACEHOLDER: adding and binding the parameters is being built separately. Until
then this step only reports which parameters in parameters.json are already in
the model and bound to the configured categories. Replace the body of run() with
the real implementation; keep the run(ctx, data) signature and report each
parameter with ctx.add(name, status.<STATUS>, message).
"""

# System Modules
from System import Guid

# 3rd Party Modules
from pyrevit import DB

# Custom Modules
from pdso.code.setup import revit_utils, status


def run(ctx, data):
    categories = []
    for category_name in data.get("categories", []):
        category = revit_utils.resolve_category(ctx.doc, category_name)
        if category is None:
            ctx.add(category_name, status.FAILED, "Category is not in this model")
        else:
            categories.append(category)

    bound = _bound_parameter_categories(ctx.doc)

    for spec in data.get("parameters", []):
        name = spec.get("name", "")
        shared = DB.SharedParameterElement.Lookup(ctx.doc, Guid(spec["guid"]))
        if shared is None:
            ctx.add(name, status.SKIPPED, "Not in the model (adding parameters isn't built yet)")
            continue

        missing = [
            c.Name for c in categories if c.Id not in bound.get(shared.Name, set())
        ]
        if missing:
            ctx.add(
                name,
                status.WARNING,
                "In the model but not bound to: {}".format(", ".join(missing)),
            )
        else:
            ctx.add(name, status.SKIPPED, "Already in the model")


def _bound_parameter_categories(doc):
    """{parameter name: set of category ElementIds it's bound to}."""
    bound = {}
    iterator = doc.ParameterBindings.ForwardIterator()
    while iterator.MoveNext():
        bound[iterator.Key.Name] = set(c.Id for c in iterator.Current.Categories)
    return bound

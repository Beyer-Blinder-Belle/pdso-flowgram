# -*- coding: utf-8 -*-
"""Model Setup step: create or update view templates listed in view_templates.json."""

# 3rd Party Modules
from pyrevit import DB
from rpw.utils.dotnet import List

# Custom Modules
from pdso.code.setup import revit_utils, status

VISIBILITY_MODES = ("unchanged", "visible", "hidden")


def run(ctx, data):
    on_existing = data.get("on_existing", "update")
    for spec in data.get("view_templates", []):
        if not spec.get("enabled", True):
            continue
        name = ctx.expand(spec.get("name", ""))
        try:
            _create_or_update_template(ctx, spec, name, on_existing)
        except Exception as error:
            ctx.add(name, status.FAILED, str(error))


def _create_or_update_template(ctx, spec, name, on_existing):
    view_type, view_family = revit_utils.view_type_info(spec.get("view_type"))
    template = revit_utils.find_view_template(ctx.doc, name)

    if template is not None:
        if on_existing == "skip":
            ctx.add(name, status.SKIPPED, "Already exists")
            return
        if template.ViewType != view_type:
            ctx.add(
                name,
                status.FAILED,
                "A {} template with this name already exists".format(
                    template.ViewType
                ),
            )
            return
        result = status.UPDATED
    else:
        result = status.CREATED

    with ctx.transaction("Create view template {}".format(name)):
        if template is None:
            template = _new_template(ctx.doc, view_type, view_family)
            template.Name = name
        warnings = apply_view_settings(ctx.doc, template, spec)

    if warnings:
        ctx.add(name, status.WARNING, "{}: {}".format(result, "; ".join(warnings)))
    else:
        ctx.add(name, result)


def _new_template(doc, view_type, view_family):
    """Create a view template of the given type.

    Uses an existing view of that type as the seed when there is one, so no view
    has to be created and deleted. Area plans in particular hold Areas, so an
    existing area plan is never deleted.
    """
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
        if not view.IsTemplate and view.ViewType == view_type:
            return view.CreateViewTemplate()

    seed = _new_seed_view(doc, view_type, view_family)
    template = seed.CreateViewTemplate()
    doc.Delete(seed.Id)
    return template


def _new_seed_view(doc, view_type, view_family):
    """Create a throwaway view of the given type to base a template on."""
    if view_type == DB.ViewType.ThreeD:
        vft = revit_utils.find_view_family_type(doc, view_family)
        if vft is None:
            raise ValueError("This model has no 3D view type")
        return DB.View3D.CreateIsometric(doc, vft.Id)

    levels, _ = revit_utils.get_levels(doc)
    if not levels:
        raise ValueError("This model has no levels")

    if view_type == DB.ViewType.AreaPlan:
        schemes = list(DB.FilteredElementCollector(doc).OfClass(DB.AreaScheme))
        if not schemes:
            raise ValueError("This model has no area schemes")
        return DB.ViewPlan.CreateAreaPlan(doc, schemes[0].Id, levels[0].Id)

    vft = revit_utils.find_view_family_type(doc, view_family)
    if vft is None:
        raise ValueError("This model has no {} view type".format(view_family))
    return DB.ViewPlan.Create(doc, vft.Id, levels[0].Id)


def apply_view_settings(doc, view, spec):
    """Apply the settings in a view template spec to a view or template.

    Must be called inside a transaction.

    Returns:
        list[str]: Problems that didn't stop the rest of the settings being applied.
    """
    warnings = []

    def attempt(description, action):
        try:
            action()
        except Exception as error:
            warnings.append("{}: {}".format(description, error))

    if "scale" in spec:
        attempt("scale", lambda: setattr(view, "Scale", int(spec["scale"])))
    if "detail_level" in spec:
        attempt(
            "detail_level",
            lambda: setattr(
                view,
                "DetailLevel",
                revit_utils.enum_value(
                    DB.ViewDetailLevel, spec["detail_level"], "detail level"
                ),
            ),
        )
    if "display_style" in spec:
        attempt(
            "display_style",
            lambda: setattr(
                view,
                "DisplayStyle",
                revit_utils.enum_value(
                    DB.DisplayStyle, spec["display_style"], "display style"
                ),
            ),
        )

    _apply_visibility_defaults(doc, view, spec.get("visibility", {}), warnings)

    for category_spec in spec.get("categories", []):
        _apply_category(doc, view, category_spec, warnings)

    if view.IsTemplate and "uncontrolled_parameters" in spec:
        attempt(
            "uncontrolled_parameters",
            lambda: _set_uncontrolled_parameters(
                view, spec["uncontrolled_parameters"]
            ),
        )

    return warnings


def _apply_visibility_defaults(doc, view, visibility, warnings):
    """Show or hide every model / annotation category, and imports, before per-category settings."""
    category_types = (
        ("model_categories", DB.CategoryType.Model),
        ("annotation_categories", DB.CategoryType.Annotation),
    )
    for key, category_type in category_types:
        mode = visibility.get(key, "unchanged")
        if mode not in VISIBILITY_MODES:
            warnings.append(
                "{} must be one of {}".format(key, ", ".join(VISIBILITY_MODES))
            )
            continue
        if mode == "unchanged":
            continue
        for category in doc.Settings.Categories:
            if category.CategoryType == category_type and view.CanCategoryBeHidden(
                category.Id
            ):
                view.SetCategoryHidden(category.Id, mode == "hidden")

    imports = visibility.get("import_categories", "unchanged")
    if imports in ("visible", "hidden"):
        try:
            view.AreImportCategoriesHidden = imports == "hidden"
        except Exception as error:
            warnings.append("import_categories: {}".format(error))


def _apply_category(doc, view, spec, warnings):
    """Apply visibility and graphic overrides for one category spec."""
    name = spec.get("category")
    category = revit_utils.resolve_category(doc, name)
    if category is None:
        warnings.append("category '{}' is not in this model".format(name))
        return

    try:
        if "visible" in spec:
            if view.CanCategoryBeHidden(category.Id):
                view.SetCategoryHidden(category.Id, not spec["visible"])
            else:
                warnings.append(
                    "'{}' can't be shown or hidden in this view".format(name)
                )

        overrides = revit_utils.build_overrides(doc, spec)
        if overrides is not None:
            view.SetCategoryOverrides(category.Id, overrides)
    except Exception as error:
        warnings.append("'{}': {}".format(name, error))


def _set_uncontrolled_parameters(template, parameter_names):
    """Stop the template controlling the listed BuiltInParameters."""
    ids = List[DB.ElementId]()
    for parameter_name in parameter_names:
        bip = revit_utils.enum_value(
            DB.BuiltInParameter, parameter_name, "BuiltInParameter"
        )
        ids.Add(DB.ElementId(bip))
    template.SetNonControlledTemplateParameterIds(ids)

# -*- coding: utf-8 -*-
"""Quick Setup step: create working views listed in views.json."""

# 3rd Party Modules
from pyrevit import DB

# Custom Modules
from pdso.code.setup import revit_utils, status


def run(ctx, data):
    on_existing = data.get("on_existing", "skip")
    for spec in data.get("views", []):
        if not spec.get("enabled", True):
            continue
        try:
            _create_views(ctx, spec, on_existing)
        except Exception as error:
            ctx.add(ctx.expand(spec.get("name", "")), status.FAILED, str(error))


def _create_views(ctx, spec, on_existing):
    view_type, view_family = revit_utils.view_type_info(spec.get("view_type"))

    template = None
    template_name = spec.get("template")
    if template_name:
        template_name = ctx.expand(template_name)
        template = revit_utils.find_view_template(ctx.doc, template_name)

    if view_type == DB.ViewType.ThreeD:
        name = ctx.expand(spec["name"])
        _create_view(
            ctx,
            spec,
            name,
            view_type,
            on_existing,
            template,
            template_name,
            lambda: _new_3d_view(ctx.doc, view_family),
        )
        return

    scheme = None
    scheme_name = None
    if view_type == DB.ViewType.AreaPlan:
        scheme_name = ctx.area_scheme_name(spec.get("area_scheme", ""))
        scheme = revit_utils.find_area_scheme(ctx.doc, scheme_name)
        if scheme is None:
            ctx.add(
                ctx.expand(
                    spec["name"], area_scheme=scheme_name, level="(all levels)"
                ),
                status.FAILED,
                "Area scheme '{}' is not in this model. Create it in Area and Volume "
                "Computations, then run setup again.".format(scheme_name),
            )
            return
    else:
        vft = revit_utils.find_view_family_type(ctx.doc, view_family)
        if vft is None:
            raise ValueError("This model has no {} view type".format(view_family))

    levels, missing = revit_utils.get_levels(ctx.doc, spec.get("levels", "all"))
    for level_name in missing:
        ctx.add(
            ctx.expand(spec["name"], level=level_name, area_scheme=scheme_name),
            status.FAILED,
            "Level '{}' is not in this model".format(level_name),
        )

    for level in levels:
        name = ctx.expand(spec["name"], level=level.Name, area_scheme=scheme_name)
        if scheme is not None:
            factory = lambda level=level: DB.ViewPlan.CreateAreaPlan(
                ctx.doc, scheme.Id, level.Id
            )
        else:
            factory = lambda level=level: DB.ViewPlan.Create(
                ctx.doc, vft.Id, level.Id
            )
        _create_view(
            ctx, spec, name, view_type, on_existing, template, template_name, factory
        )


def _new_3d_view(doc, view_family):
    vft = revit_utils.find_view_family_type(doc, view_family)
    if vft is None:
        raise ValueError("This model has no 3D view type")
    return DB.View3D.CreateIsometric(doc, vft.Id)


def _create_view(
    ctx, spec, name, view_type, on_existing, template, template_name, factory
):
    """Create one view (or update an existing one), apply its template and parameters."""
    view = revit_utils.find_view(ctx.doc, name, view_type)

    if view is not None and on_existing == "skip":
        ctx.add(name, status.SKIPPED, "Already exists")
        return

    warnings = []
    try:
        with ctx.transaction("Create view {}".format(name)):
            result = status.UPDATED
            if view is None:
                view = factory()
                view.Name = name
                result = status.CREATED

            if template is not None:
                view.ViewTemplateId = template.Id
            elif template_name:
                warnings.append(
                    "view template '{}' not found; run the view_templates step".format(
                        template_name
                    )
                )

            for parameter_name, value in spec.get("parameters", {}).items():
                try:
                    revit_utils.set_parameter_value(
                        view, parameter_name, ctx.expand(value)
                    )
                except Exception as error:
                    warnings.append(str(error))
    except Exception as error:
        ctx.add(name, status.FAILED, str(error))
        return

    if warnings:
        ctx.add(name, status.WARNING, "{}: {}".format(result, "; ".join(warnings)))
    else:
        ctx.add(name, result, "Template: {}".format(template_name or "none"))

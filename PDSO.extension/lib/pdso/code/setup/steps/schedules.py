# -*- coding: utf-8 -*-
"""Model Setup step: create schedules listed in schedules.json."""

# 3rd Party Modules
from pyrevit import DB

# Custom Modules
from pdso.code.setup import revit_utils, status

# schedules.json operator -> ScheduleFilterType member name
FILTER_OPERATORS = {
    "equals": "Equal",
    "not_equals": "NotEqual",
    "greater": "GreaterThan",
    "greater_or_equal": "GreaterThanOrEqual",
    "less": "LessThan",
    "less_or_equal": "LessThanOrEqual",
    "contains": "Contains",
    "not_contains": "NotContains",
    "begins_with": "BeginsWith",
    "not_begins_with": "NotBeginsWith",
    "ends_with": "EndsWith",
    "not_ends_with": "NotEndsWith",
    "has_value": "HasValue",
    "has_no_value": "HasNoValue",
}
NO_VALUE_OPERATORS = ("has_value", "has_no_value")


def run(ctx, data):
    on_existing = data.get("on_existing", "skip")
    for spec in data.get("schedules", []):
        if not spec.get("enabled", True):
            continue
        name = ctx.expand(spec.get("name", ""))
        try:
            _create_schedule(ctx, spec, name, on_existing)
        except Exception as error:
            ctx.add(name, status.FAILED, str(error))


def _create_schedule(ctx, spec, name, on_existing):
    existing = revit_utils.find_view(ctx.doc, name, view_class=DB.ViewSchedule)
    if existing is not None:
        if on_existing == "skip":
            ctx.add(name, status.SKIPPED, "Already exists")
            return
        if on_existing != "replace":
            raise ValueError(
                "on_existing must be 'skip' or 'replace', not '{}'".format(on_existing)
            )

    category = revit_utils.resolve_category(ctx.doc, spec.get("category"))
    if category is None:
        raise ValueError(
            "Category '{}' is not in this model".format(spec.get("category"))
        )

    warnings = []
    with ctx.transaction("Create schedule {}".format(name)):
        if existing is not None:
            ctx.doc.Delete(existing.Id)
        schedule = DB.ViewSchedule.CreateSchedule(ctx.doc, category.Id)
        schedule.Name = name
        _build_definition(ctx, schedule, spec, warnings)

    result = status.UPDATED if existing is not None else status.CREATED
    if warnings:
        ctx.add(name, status.WARNING, "{}: {}".format(result, "; ".join(warnings)))
    else:
        ctx.add(name, result)


def _build_definition(ctx, schedule, spec, warnings):
    definition = schedule.Definition

    schedulable = {}
    for schedulable_field in definition.GetSchedulableFields():
        schedulable.setdefault(schedulable_field.GetName(ctx.doc), schedulable_field)

    added = {}

    def get_field(parameter_name, hidden_if_new=False):
        """The schedule field for a parameter, adding it if needed."""
        if parameter_name in added:
            return added[parameter_name]
        if parameter_name not in schedulable:
            warnings.append(
                "'{}' is not a schedulable field (is the parameter in the model?)".format(
                    parameter_name
                )
            )
            return None
        field = definition.AddField(schedulable[parameter_name])
        if hidden_if_new:
            field.IsHidden = True
        added[parameter_name] = field
        return field

    for field_spec in spec.get("fields", []):
        field = get_field(field_spec["parameter"])
        if field is None:
            continue
        if "heading" in field_spec:
            field.ColumnHeading = ctx.expand(field_spec["heading"])
        if field_spec.get("hidden"):
            field.IsHidden = True
        if field_spec.get("totals"):
            _enable_totals(field, field_spec["parameter"], warnings)

    for filter_spec in spec.get("filters", []):
        field = get_field(filter_spec["parameter"], hidden_if_new=True)
        if field is None:
            continue
        try:
            definition.AddFilter(_build_filter(ctx, field, filter_spec))
        except Exception as error:
            warnings.append(
                "filter on '{}': {}".format(filter_spec["parameter"], error)
            )

    for sort_spec in spec.get("sort", []):
        field = get_field(sort_spec["parameter"], hidden_if_new=True)
        if field is None:
            continue
        order = (
            DB.ScheduleSortOrder.Descending
            if sort_spec.get("order", "ascending") == "descending"
            else DB.ScheduleSortOrder.Ascending
        )
        sort_field = DB.ScheduleSortGroupField(field.FieldId, order)
        sort_field.ShowHeader = bool(sort_spec.get("header", False))
        sort_field.ShowFooter = bool(sort_spec.get("footer", False))
        definition.AddSortGroupField(sort_field)

    if "itemize" in spec:
        definition.IsItemized = bool(spec["itemize"])
    if "grand_totals" in spec:
        definition.ShowGrandTotal = bool(spec["grand_totals"])


def _build_filter(ctx, field, filter_spec):
    operator = filter_spec.get("operator", "equals")
    if operator not in FILTER_OPERATORS:
        raise ValueError(
            "unknown operator '{}'. Use one of: {}".format(
                operator, ", ".join(sorted(FILTER_OPERATORS))
            )
        )
    filter_type = getattr(DB.ScheduleFilterType, FILTER_OPERATORS[operator])

    if operator in NO_VALUE_OPERATORS:
        return DB.ScheduleFilter(field.FieldId, filter_type)

    value = ctx.expand(filter_spec.get("value"))
    if value is None or (isinstance(value, basestring) and "{" in value):  # noqa: F821
        raise ValueError("value '{}' could not be resolved".format(value))
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, float):
        return DB.ScheduleFilter(field.FieldId, filter_type, float(value))
    if isinstance(value, int):
        return DB.ScheduleFilter(field.FieldId, filter_type, int(value))
    return DB.ScheduleFilter(field.FieldId, filter_type, u"{}".format(value))


def _enable_totals(field, parameter_name, warnings):
    if not field.CanTotal():
        warnings.append("'{}' can't be totaled".format(parameter_name))
        return
    try:
        # Revit 2022+
        field.DisplayType = DB.ScheduleFieldDisplayType.Totals
    except AttributeError:
        field.HasTotals = True

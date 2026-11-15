# -*- coding: utf-8 -*-
"""Quick Setup steps.

Each step is a function run(ctx, data): ctx is a runner.SetupContext and data is
the parsed <step id>.json standards file. To add a step, write a module here,
register it below, and list its id in setup.json.

Parameters aren't a setup step: the Parameter Manager creates and binds them, and
users run it before Quick Setup.
"""

from pdso.code.setup.steps import (
    area_schemes,
    families,
    schedules,
    view_templates,
    views,
)

STEP_RUNNERS = {
    "area_schemes": area_schemes.run,
    "families": families.run,
    "view_templates": view_templates.run,
    "views": views.run,
    "schedules": schedules.run,
}

# -*- coding: utf-8 -*-
"""Model Setup steps.

Each step is a function run(ctx, data): ctx is a runner.SetupContext and data is
the parsed <step id>.json standards file. To add a step, write a module here,
register it below, and list its id in setup.json.
"""

from pdso.code.setup.steps import (
    families,
    parameters,
    schedules,
    view_templates,
    views,
)

STEP_RUNNERS = {
    "parameters": parameters.run,
    "families": families.run,
    "view_templates": view_templates.run,
    "views": views.run,
    "schedules": schedules.run,
}

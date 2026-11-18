# -*- coding: utf-8 -*-
"""PDSO Quick Setup.

Run after Project Config and the Parameter Manager. Creates the area schemes from
Project Config, loads the Data Silo family, and creates view templates, working
views and schedules, all from JSON standards files. Safe to run again: items that
already exist are skipped or updated as each standards file says.

Shift+Click to choose or export a standards folder.
"""

# 3rd Party Modules
from pyrevit import forms, revit, script

# Custom Modules
from pdso.code.setup.runner import (
    SetupStep,
    missing_prerequisites,
    print_report,
    run_setup,
)
from pdso.code.setup.standards import Standards, StandardsError


class _StepOption(forms.TemplateListItem):
    """Shows a setup step by its title in the step picker."""

    @property
    def name(self):
        return self.item.title


if __name__ == "__main__":
    doc = revit.doc
    output = script.get_output()

    # Setup order: Project Config, then Parameter Manager, then Quick Setup
    missing = missing_prerequisites(doc)
    if missing and not forms.alert(
        "Run Project Config and the Parameter Manager before Quick Setup.",
        sub_msg=" ".join(missing) + " Run Quick Setup anyway?",
        title="PDSO Quick Setup",
        yes=True,
        no=True,
    ):
        script.exit()

    standards = Standards(doc)
    try:
        steps = SetupStep.from_standards(standards.load("setup.json"))
    except StandardsError as error:
        forms.alert(str(error), title="PDSO Quick Setup", exitscript=True)

    selected = forms.SelectFromList.show(
        [_StepOption(step, checked=step.enabled) for step in steps],
        title="PDSO Quick Setup",
        button_name="Run Setup",
        multiselect=True,
        width=500,
        height=400,
    )
    if not selected:
        script.exit()

    output.set_title("PDSO Quick Setup")
    output.print_md("# PDSO Quick Setup")

    ctx = run_setup(doc, standards, selected)
    print_report(ctx, output)

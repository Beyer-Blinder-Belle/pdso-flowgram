# -*- coding: utf-8 -*-
"""PDSO Model Setup.

Adds the PDSO parameters, loads the Data Silo family, and creates view templates,
working views and schedules, all from JSON standards files. Safe to run again:
items that already exist are skipped or updated as each standards file says.

Shift+Click to choose or export a standards folder.
"""

# 3rd Party Modules
from pyrevit import forms, revit, script

# Custom Modules
from pdso.code.setup.runner import SetupStep, print_report, run_setup
from pdso.code.setup.standards import Standards, StandardsError


class _StepOption(forms.TemplateListItem):
    """Shows a setup step by its title in the step picker."""

    @property
    def name(self):
        return self.item.title


if __name__ == "__main__":
    doc = revit.doc
    output = script.get_output()

    standards = Standards(doc)
    try:
        steps = SetupStep.from_standards(standards.load("setup.json"))
    except StandardsError as error:
        forms.alert(str(error), title="PDSO Model Setup", exitscript=True)

    selected = forms.SelectFromList.show(
        [_StepOption(step, checked=step.enabled) for step in steps],
        title="PDSO Model Setup",
        button_name="Run Setup",
        multiselect=True,
        width=500,
        height=400,
    )
    if not selected:
        script.exit()

    output.set_title("PDSO Model Setup")
    output.print_md("# PDSO Model Setup")

    ctx = run_setup(doc, standards, selected)
    print_report(ctx, output)

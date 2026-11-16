# -*- coding: utf-8 -*-
"""Sync Data: copy Area SF to Data Silos and update the Program Volumes."""

# pyRevit Modules
from pyrevit import script
from pyrevit.revit.ui import get_mainwindow

# System/ Windows Modules
from System.Windows import Application
from System.Reflection import Assembly

# Custom Modules
from pdso.code.view_models.body_2_harvest_areas import PDSO_View_Model_2_Harvest_Areas

# Each PDSO window has its own Uid, so one button never brings up another
# button's window (they all load 0_window.xaml)
WINDOW_UID = "bbbtools-pdso-sync-window"

if __name__ == "__main__":
    # Set the ResourceAssembly to the current executing assembly
    Application.ResourceAssembly = Assembly.GetExecutingAssembly()

    # Get the Revit window
    revit_window = get_mainwindow()

    # Reuse this window if it's already open
    custom_ui_window = next(
        (
            window
            for window in revit_window.OwnedWindows
            if window.Uid == WINDOW_UID
        ),
        None,
    )

    if custom_ui_window is not None:
        custom_ui_window.Activate()

    else:
        view_model = PDSO_View_Model_2_Harvest_Areas()
        custom_ui_window = script.load_ui(
            view_model, "../../../lib/pdso/xaml/0_window.xaml", True, True
        )

        custom_ui_window.Uid = WINDOW_UID
        custom_ui_window.Owner = revit_window
        custom_ui_window.DataContext = view_model
        custom_ui_window.Title = "Program Data Silo Tools - Sync Data"

        custom_ui_window.show()

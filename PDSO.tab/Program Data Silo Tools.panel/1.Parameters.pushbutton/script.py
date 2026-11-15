# -*- coding: utf-8 -*-

# pyRevit Modules
from pyrevit import script
from pyrevit.revit.ui import get_mainwindow

# System/ Windows Modules
from System.Windows import Application
from System.Reflection import Assembly

# Custom Modules
from pdso.code.view_models.body_3_parameters import PDSO_View_Model_3_Parameters

WINDOW_UID = "bbbtools-pdso-parameters-window"

if __name__ == "__main__":
    # Set the ResourceAssembly to the current executing assembly
    Application.ResourceAssembly = Assembly.GetExecutingAssembly()

    # Get the Revit window
    revit_window = get_mainwindow()

    # Reuse the Parameter Manager window if it's already open
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
        view_model = PDSO_View_Model_3_Parameters()
        custom_ui_window = script.load_ui(
            view_model, "../../../lib/pdso/xaml/0_window.xaml", True, True
        )

        # Give this window its own Uid so it doesn't collide with the other PDSO windows
        custom_ui_window.Uid = WINDOW_UID
        custom_ui_window.Owner = revit_window
        custom_ui_window.DataContext = view_model
        custom_ui_window.Title = "Program Data Silo Tools - Parameter Manager"
        custom_ui_window.Width = 640

        custom_ui_window.show()

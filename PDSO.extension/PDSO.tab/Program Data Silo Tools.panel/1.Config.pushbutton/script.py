# -*- coding: utf-8 -*-

# pyRevit Modules
from pyrevit import script
from pyrevit.revit.ui import get_mainwindow

# Python standard libraries
# None

# System/ Windows Modules
from System.Windows import Application
from System.Reflection import Assembly

# Custom Modules
from pdso.code.view_models.body_1_setup import PDSO_View_Model_1_Setup
# from pdso.code.view.view_shimmer import shim_view_elements

if __name__ == "__main__":
    # Set the ResourceAssembly to the current executing assembly
    Application.ResourceAssembly = Assembly.GetExecutingAssembly()

    # Get the Revit window
    revit_window = get_mainwindow()

    # Look through the windows owned by the Revit window to try and find an instance of the custom UI window
    custom_ui_window = next(
        (
            window
            for window in revit_window.OwnedWindows
            if window.Uid == "bbbtools-pdso-window"
        ),
        None,
    )

    if custom_ui_window != None:
        # If the custom UI window is already opened, show it with .Activate()
        custom_ui_window.Activate()

    else:
        # If the custom UI window is not currently open, create a new instance
        # Get the logger and output from the main script context to ensure all logging goes to the same console

        # Create ViewModel instance
        view_model = PDSO_View_Model_1_Setup()

        # Load the wrapper window XAML and bind to ViewModel
        custom_ui_window = script.load_ui(
            view_model, "../../../lib/pdso/xaml/0_window.xaml", True, True
        )

        # Set the owner of the custom UI window to the Revit main window
        custom_ui_window.Owner = revit_window
        custom_ui_window.DataContext = view_model
        custom_ui_window.Title = "Program Data Silo Tools - Setup"

        # Show the custom UI window
        custom_ui_window.show()

# -*- coding: utf-8 -*-
"""Main PDSO View Model - Refactored and Simplified.

This is the main view model class that coordinates between the UI and the business logic.
It has been refactored to be much smaller and more focused by delegating specific
responsibilities to specialized classes.
"""

# Python standard libraries
import time
import traceback

# 3rd Party Modules

# System/ Windows Modules
import System.Threading as Threading
import System

# Custom Modules
from base_view_model import PDSO_Base_View_Model


class PDSO_View_Model_Example(PDSO_Base_View_Model):
    """PDSO Custom Display Window

    This view model acts as the main coordinator between the UI and business logic.
    It delegates specific responsibilities to specialized helper classes:
    - PDSOViewSetup: Handles UI setup, XAML loading, and event wiring
    - PDSOProcessController: Handles validation and data synchronization logic

    Attributes:
        output: PyRevit rich output for user-friendly display
        logger: PyRevit logger for debug-level logging
        dynamic_ui_elements: List of dynamically created UI components
        custom_event_handler: Handler for external events
        process_controller: Handler for business logic operations
    """

    def __init__(self):
        """Initialize the view model"""
        super(PDSO_View_Model_Example, self).__init__()

        # Progress bar demo properties
        self._progress_status = "Ready to start demo"
        self._progress_value = 0
        self._progress_max = 100
        self._counter_value = 0

    def setup(self):
        """Set up the UI components and event handlers"""
        super(PDSO_View_Model_Example, self).setup("1_setup.xaml")

        # Wire up the counter demo button
        self._wire_counter_button()

    def _wire_counter_button(self):
        """Wire up the progress demo button click event"""
        try:
            # Debug: Check if method exists
            self.logger.info("Checking for find_element_by_name method...")
            self.logger.info(
                "Has method: {}".format(hasattr(self, "find_element_by_name"))
            )
            self.logger.info("Base class: {}".format(PDSO_Base_View_Model))

            # Find the ContentPresenter that holds the loaded XAML
            presenter = self.FindName("BodyContent")

            if presenter and presenter.Content:
                # Search for the button in the loaded content - call parent class method directly
                start_button = self.find_element_by_name(
                    presenter.Content, "StartProgressButton"
                )
                if start_button:
                    start_button.Click += self._on_start_progress_clicked
                    self.logger.info("Progress demo button wired successfully")
                else:
                    self.logger.warning("StartProgressButton not found in content")
            else:
                self.logger.warning("BodyContent presenter or content not found")
        except Exception as e:
            self.logger.error("Error wiring progress button: {}".format(e))

    def _on_start_progress_clicked(self, sender, args):
        """Handle progress demo button click event

        Args:
            sender: Button that triggered the event
            args: Event arguments
        """
        self.logger.info("Progress demo button clicked!")
        try:
            self.start_progress_demo()
        except Exception as e:
            self.logger.error("Error starting progress demo: {}".format(e))

    # Progress demo properties for data binding
    @property
    def progress_status(self):
        """Get current progress status for UI binding

        Returns:
            str: Current progress status message
        """
        return self._progress_status

    @progress_status.setter
    def progress_status(self, value):
        """Set progress status and notify UI

        Args:
            value (str): New progress status message
        """
        if self._progress_status != value:
            self._progress_status = value
            self.on_property_changed("progress_status")

    @property
    def progress_value(self):
        """Get current progress value for ProgressBar binding

        Returns:
            int: Current progress value
        """
        return self._progress_value

    @progress_value.setter
    def progress_value(self, value):
        """Set progress value and notify UI

        Args:
            value (int): New progress value
        """
        if self._progress_value != value:
            self._progress_value = value
            self.on_property_changed("progress_value")

    @property
    def progress_max(self):
        """Get maximum progress value for ProgressBar binding

        Returns:
            int: Maximum progress value
        """
        return self._progress_max

    @progress_max.setter
    def progress_max(self, value):
        """Set maximum progress value and notify UI

        Args:
            value (int): New maximum progress value
        """
        if self._progress_max != value:
            self._progress_max = value
            self.on_property_changed("progress_max")

    @property
    def counter_value(self):
        """Get current counter value for UI binding

        Returns:
            int: Current counter value
        """
        return self._counter_value

    @counter_value.setter
    def counter_value(self, value):
        """Set counter value and notify UI

        Args:
            value (int): New counter value
        """
        if self._counter_value != value:
            self._counter_value = value
            self.on_property_changed("counter_value")

    def start_progress_demo(self):
        """Start proof-of-concept progress bar demo

        Demonstrates:
        - WPF ProgressBar control with data binding
        - Background threading with progress updates
        - Dispatcher for thread-safe UI updates
        - Progress status updates in ViewModel

        Note:
            Uses in-window ProgressBar control instead of pyRevit's modal ProgressBar.
            Better suited for modeless windows where you want progress visible in your UI.
        """
        self.logger.info("Progress bar demo starting...")

        # Reset progress
        self.progress_value = 0
        self.progress_max = 100
        self.progress_status = "Starting demo..."

        # Create thread for the demo work
        thread = Threading.Thread(Threading.ThreadStart(self._progress_demo_work))
        thread.IsBackground = True
        thread.Start()
        self.logger.info("Progress demo thread started")

    def _progress_demo_work(self):
        """Background thread that updates progress bar via data binding

        Demonstrates a typical use case: iterating through items with progress.
        Updates are thread-safe using Dispatcher to modify bound properties.
        """
        self.logger.info(">>> Inside _progress_demo_work - thread executing")
        try:
            max_items = 100
            self.logger.info("Starting loop for {} items".format(max_items))

            for i in range(max_items):
                # Simulate work
                time.sleep(0.05)

                # Update counter and progress value on UI thread
                current = i + 1
                self.Dispatcher.Invoke(
                    System.Action(
                        lambda val=current: setattr(self, "counter_value", val)
                    )
                )
                self.Dispatcher.Invoke(
                    System.Action(
                        lambda val=current: setattr(self, "progress_value", val)
                    )
                )

                # Update status every 10 items
                if current % 10 == 0:
                    self.logger.info("Progress: {} of {}".format(current, max_items))
                    status_msg = "Processed {} of {} items...".format(
                        current, max_items
                    )
                    self.Dispatcher.Invoke(
                        System.Action(
                            lambda msg=status_msg: setattr(self, "progress_status", msg)
                        )
                    )

            # Completed successfully
            self.logger.info("Demo completed successfully")
            self.Dispatcher.Invoke(
                System.Action(
                    lambda: setattr(
                        self, "progress_status", "Demo completed! Processed 100 items."
                    )
                )
            )

        except Exception as e:
            self.logger.error("Progress demo error: {}".format(e))
            self.logger.error("Traceback: {}".format(traceback.format_exc()))
            self.Dispatcher.Invoke(
                System.Action(lambda: setattr(self, "progress_status", "Demo failed!"))
            )

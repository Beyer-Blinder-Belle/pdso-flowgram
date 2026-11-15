# -*- coding: utf-8 -*-
"""Main PDSO View Model - Refactored and Simplified.

This is the main view model class that coordinates between the UI and the business logic.
It has been refactored to be much smaller and more focused by delegating specific
responsibilities to specialized classes.
"""

# System/ Windows Modules
from System import Uri
from System.Windows import ResourceDictionary
import System.Windows.Media
from System.Windows.Markup import XamlReader
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs


# Python standard libraries
import os

# 3rd Party Modules
from pyrevit import forms, script

# Custom Modules
from common.code import generic_utils
from common.code.view_model_external_events import CustomizableEvent
from pdso.code.business.process_controller import PDSOProcessController


class PDSO_Base_View_Model(forms.WPFWindow, INotifyPropertyChanged):
    """PDSO Custom Display Window

    This view model acts as the main coordinator between the UI and business logic.
    It delegates specific responsibilities to specialized helper classes:
    - PDSOViewSetup: Handles UI setup, XAML loading, and event wiring
    - PDSOProcessController: Handles validation and data synchronization logic

    Implements INotifyPropertyChanged for WPF data binding support.

    Attributes:
        output: PyRevit rich output for user-friendly display
        logger: PyRevit logger for debug-level logging
        dynamic_ui_elements: List of dynamically created UI components
        custom_event_handler: Handler for external events
        process_controller: Handler for business logic operations
    """

    def __init__(self):
        """Initialize the view model"""
        self.logger = script.get_logger()
        self.output = script.get_output()

        # Initialize core components
        self.dynamic_ui_elements = []
        self.custom_event_handler = CustomizableEvent()

        # Initialize helper classes - pass logger_manager instead of output
        # ProcessController will access output through logger_manager
        self.process_controller = PDSOProcessController()

        # INotifyPropertyChanged implementation
        self._property_changed_handlers = []

    def setup(self, xaml_file_name):
        """Complete setup of the view model (called after window creation)"""
        self.logger.info("🎨 Start: View model setup process")

        # Link WPF resource libraries
        self.logger.info("Start: Linking WPF resources")
        self.link_wpf_resources()
        self.logger.info("Completed: Linking WPF resources")

        # Load external XAML content
        self.logger.info("Start: Loading external XAML content")
        self.load_window_body_content(xaml_file_name)
        self.logger.info("Completed: Loading external XAML content")

        # Fix linked image resources
        self.logger.info("Start: Fixing image sources")
        self.fix_image_sources()
        self.logger.info("Completed: Fixing image sources")

        # Setup dynamic UI elements (must be after XAML loading)
        # self.logger.debug("Start: Setting up dynamic UI elements")
        # setup_dynamic_ui_elements(self)
        # self.logger.debug("Completed: Setting up dynamic UI elements")

        self.logger.info("🎨 Completed: View model setup process")

    # INotifyPropertyChanged implementation for WPF data binding
    def add_PropertyChanged(self, handler):
        """Add handler for PropertyChanged event (INotifyPropertyChanged interface)

        Args:
            handler: Event handler for property changes
        """
        self._property_changed_handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        """Remove handler for PropertyChanged event (INotifyPropertyChanged interface)

        Args:
            handler: Event handler to remove
        """
        if handler in self._property_changed_handlers:
            self._property_changed_handlers.remove(handler)

    def on_property_changed(self, property_name):
        """Raise PropertyChanged event to notify UI of property changes

        Args:
            property_name (str): Name of the property that changed

        Notes:
            Call this method whenever a bound property value changes.
            The UI will automatically update if bound via {Binding property_name}.
        """
        args = PropertyChangedEventArgs(property_name)
        for handler in self._property_changed_handlers:
            handler(self, args)

    def link_wpf_resources(self):
        """Setup WPF resource dictionary and styles"""
        lib_path = generic_utils.lib_path()
        path_styles = os.path.join(lib_path, "pdso", "xaml", "WPF_styles.xaml")

        # Convert the file path to a URI
        path_styles_uri = "file:///" + path_styles.replace("\\", "/")

        r = ResourceDictionary()
        try:
            r.Source = Uri(path_styles_uri)
            self.Resources = r
        except Exception as e:
            self.logger.warning(
                "Failed to load WPF styles from {}: {}".format(path_styles_uri, e)
            )
            # Silently handle resource loading errors
            pass

    def load_window_body_content(self, xaml_file_name):
        """Load external XAML files and set them to ContentPresenter elements"""
        try:
            ui_path = os.path.join(generic_utils.lib_path(), "pdso", "xaml")

            xaml_path = os.path.join(ui_path, xaml_file_name)

            # Check if file exists
            if not os.path.exists(xaml_path):
                self.logger.error("XAML file not found: {}".format(xaml_path))
                return None

            # Read the XAML content
            with open(xaml_path, "r") as xaml_file:
                xaml_content = xaml_file.read()

            # Parse the XAML content using XamlReader.Parse
            ui_element = XamlReader.Parse(xaml_content)

            if ui_element:
                presenter = self.FindName("BodyContent")
                if presenter:
                    presenter.Content = ui_element

        except Exception as e:
            self.logger.error("Error loading external XAML content: {}".format(str(e)))

    def fix_image_sources(self, current_node=None):
        """Recursively find and fix image sources in the UI tree

        Args:
            current_node: UI element to start from, defaults to view model root
        """
        if current_node is None:
            current_node = self

        if not current_node:
            return

        # If the current object is an Image and has a Tag, fix its Source
        if hasattr(current_node, "GetType") and current_node.GetType().Name == "Image":
            if current_node.Tag:
                lib_path = generic_utils.lib_path()
                path_to_asset = os.path.join(lib_path, str(current_node.Tag))

                self.logger.debug(
                    "Found Image element: Name='{}', Tag='{}'".format(
                        getattr(current_node, "Name", "NO_NAME"), current_node.Tag
                    )
                )

                if os.path.exists(path_to_asset):
                    # Load the image and ensure it's an ImageSource
                    image_source = generic_utils.load_image_source(path_to_asset)
                    if isinstance(image_source, System.Windows.Media.ImageSource):
                        current_node.Source = image_source
                        self.logger.debug(
                            "  Successfully loaded image from: {}".format(path_to_asset)
                        )
                    else:
                        self.logger.debug(
                            "  Loaded image is not an ImageSource: {}".format(
                                path_to_asset
                            )
                        )
                else:
                    self.logger.debug(
                        "  Image asset not found: {}".format(path_to_asset)
                    )
            else:
                self.logger.debug(
                    "Found Image element without Tag: Name='{}'".format(
                        getattr(current_node, "Name", "NO_NAME")
                    )
                )

        # Handle special container types
        elif hasattr(current_node, "GetType"):
            self._fix_images_in_containers(current_node)

    def _fix_images_in_containers(self, current_node):
        """Handle image fixing for various container types"""
        type_name = current_node.GetType().Name
        node_name = getattr(current_node, "Name", "NO_NAME")

        self.logger.debug(
            "Processing container: Type='{}', Name='{}'".format(type_name, node_name)
        )

        # Handle TabControl
        if type_name == "TabControl":
            tab_items = getattr(current_node, "Items", None)
            if tab_items:
                self.logger.debug(
                    "  Found TabControl with {} items".format(len(tab_items))
                )
                for tab_item in tab_items:
                    self.fix_image_sources(tab_item)

        # Handle BulletDecorator
        elif type_name == "BulletDecorator":
            bullet = getattr(current_node, "Bullet", None)
            if bullet:
                self.logger.debug("  Found BulletDecorator with bullet")
                self.fix_image_sources(bullet)

        # Handle all elements with Children (Panels like StackPanel, Grid)
        children = getattr(current_node, "Children", None)
        if children:
            self.logger.debug("  Found {} children".format(len(children)))
            for child in children:
                self.fix_image_sources(child)

        # Handle Border elements with Child property (singular)
        child = getattr(current_node, "Child", None)
        if child:
            child_type = (
                child.GetType().Name
                if hasattr(child, "GetType")
                else type(child).__name__
            )
            self.logger.debug("  Found Child: Type='{}'".format(child_type))
            self.fix_image_sources(child)

        # Handle ContentControl elements (Button, ContentPresenter, etc.)
        content = getattr(current_node, "Content", None)
        if content:
            content_type = (
                content.GetType().Name
                if hasattr(content, "GetType")
                else type(content).__name__
            )
            self.logger.debug("  Found Content: Type='{}'".format(content_type))
            self.fix_image_sources(content)

    def find_element_by_name(self, parent, name):
        """Recursively find an element by its Name property"""
        if not parent:
            return None

        # Check if this element has the name we're looking for
        if hasattr(parent, "Name") and parent.Name == name:
            return parent

        # Recursively search children (for Panels like StackPanel, Grid)
        if hasattr(parent, "Children"):
            for child in parent.Children:
                result = self.find_element_by_name(child, name)
                if result:
                    return result

        # Check Child property for Border elements
        if hasattr(parent, "Child"):
            result = self.find_element_by_name(parent.Child, name)
            if result:
                return result

        # Check Content property for ContentControl elements
        if hasattr(parent, "Content"):
            result = self.find_element_by_name(parent.Content, name)
            if result:
                return result

        return None

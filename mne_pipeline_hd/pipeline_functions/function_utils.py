# -*- coding: utf-8 -*-
"""
Pipeline-GUI for Analysis with MNE-Python
@author: Martin Schulz
@email: dev@earthman-music.de
@github: https://github.com/marsipu/mne_pipeline_hd
License: BSD (3-clause)
Written on top of MNE-Python
Copyright © 2011-2020, authors of MNE-Python (https://doi.org/10.3389/fnins.2013.00267)
inspired by Andersen, L. M. (2018) (https://doi.org/10.3389/fnins.2018.00006)
"""
import gc
import inspect
import logging
import re
from collections import OrderedDict

from PyQt5.QtCore import QThreadPool
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel, QListView, QProgressBar,
                             QPushButton, QSizePolicy, QStyle, QVBoxLayout)
from mne_pipeline_hd.pipeline_functions.loading import BaseLoading, FSMRI, Group, MEEG

from .pipeline_utils import shutdown
from .. import QS
from ..basic_functions.plot import close_all
from ..gui.base_widgets import SimpleList
from ..gui.gui_utils import MainConsoleWidget, Worker, get_exception_tuple, get_std_icon, set_ratio_geometry
from ..gui.models import RunModel


def get_arguments(func_name, module, obj):
    keyword_arguments = {}
    project_attributes = vars(obj.pr)

    # Get arguments from function signature
    func = getattr(module, func_name)
    arg_names = list(inspect.signature(func).parameters)

    # Remove args/kwargs
    if 'args' in arg_names:
        arg_names.remove('args')
    if 'kwargs' in arg_names:
        arg_names.remove('kwargs')

    # Get the values for parameter-names
    for arg_name in arg_names:
        if arg_name == 'ct':
            keyword_arguments.update({'ct': obj.ct})
        elif arg_name == 'controller':
            keyword_arguments.update({'controller': obj.ct})
        elif arg_name == 'pr':
            keyword_arguments.update({'pr': obj.pr})
        elif arg_name == 'project':
            keyword_arguments.update({'project': obj.pr})
        elif arg_name == 'meeg':
            keyword_arguments.update({'meeg': obj})
        elif arg_name == 'fsmri':
            keyword_arguments.update({'fsmri': obj})
        elif arg_name == 'group':
            keyword_arguments.update({'group': obj})
        elif arg_name in project_attributes:
            keyword_arguments.update({arg_name: project_attributes[arg_name]})
        elif arg_name in obj.pr.parameters[obj.pr.p_preset]:
            keyword_arguments.update({arg_name: obj.pr.parameters[obj.pr.p_preset][arg_name]})
        elif arg_name in obj.ct.settings:
            keyword_arguments.update({arg_name: obj.ct.settings[arg_name]})
        elif arg_name in QS().childKeys():
            keyword_arguments.update({arg_name: QS().value(arg_name)})
        else:
            raise RuntimeError(f'{arg_name} could not be found in Subject, Project or Parameters')

    # Add additional keyword-arguments if added for function by user
    if func_name in obj.pr.add_kwargs:
        for kwarg in obj.pr.add_kwargs[func_name]:
            keyword_arguments[kwarg] = obj.pr.add_kwargs[func_name][kwarg]

    return keyword_arguments


def func_from_def(func_name, obj):
    # Get module- and package-name, has to specified in pd_funcs
    # (which imports from functions.csv or the <custom_package>.csv)
    pkg_name = obj.ct.pd_funcs.loc[func_name, 'pkg_name']
    module_name = obj.ct.pd_funcs.loc[func_name, 'module']
    module = obj.ct.all_modules[pkg_name][module_name][0]

    keyword_arguments = get_arguments(func_name, module, obj)

    # Catch one error due to unexpected or missing keywords
    unexp_kw_pattern = r"(.*) got an unexpected keyword argument \'(.*)\'"
    miss_kw_pattern = r"(.*) missing 1 required positional argument: \'(.*)\'"
    try:
        # Call Function from specified module with arguments from unpacked list/dictionary
        getattr(module, func_name)(**keyword_arguments)
    except TypeError as te:
        match_unexp_kw = re.match(unexp_kw_pattern, str(te))
        match_miss_kw = re.match(miss_kw_pattern, str(te))
        if match_unexp_kw:
            keyword_arguments.pop(match_unexp_kw.group(2))
            logging.warning(f'Caught unexpected keyword \"{match_unexp_kw.group(2)}\" for {func_name}')
            getattr(module, func_name)(**keyword_arguments)
        elif match_miss_kw:
            add_kw_args = get_arguments([match_miss_kw.group(2)], module, obj)
            keyword_arguments.update(add_kw_args)
            logging.warning(f'Caught missing keyword \"{match_miss_kw.group(2)}\" for {func_name}')
            getattr(module, func_name)(**keyword_arguments)
        else:
            raise te


class RunDialog(QDialog):
    def __init__(self, main_win):
        super().__init__(main_win)
        self.mw = main_win

        # Initialize Attributes
        self.init_attributes()

        self.init_lists()
        self.init_ui()

        set_ratio_geometry(0.6, self)
        self.show()

        self.start_thread()

    def init_attributes(self):
        # Initialize class-attributes (in method to be repeatable by self.restart)
        self.all_steps = list()
        self.thread_idx_count = 0
        self.all_objects = OrderedDict()
        self.current_all_funcs = dict()
        self.current_step = None
        self.current_object = None
        self.loaded_fsmri = None
        self.current_func = None

        self.errors = dict()
        self.error_count = 0
        self.prog_count = 0
        self.is_prog_text = False
        self.paused = False

    def init_lists(self):
        # Lists of selected functions divided into object-types (MEEG, FSMRI, ...)
        self.sel_fsmri_funcs = [mf for mf in self.mw.ct.fsmri_funcs.index if mf in self.mw.ct.pr.sel_functions]
        self.sel_meeg_funcs = [ff for ff in self.mw.ct.meeg_funcs.index if ff in self.mw.ct.pr.sel_functions]
        self.sel_group_funcs = [gf for gf in self.mw.ct.group_funcs.index if gf in self.mw.ct.pr.sel_functions]
        self.sel_other_funcs = [of for of in self.mw.ct.other_funcs.index if of in self.mw.ct.pr.sel_functions]

        # Get a dict with all objects paired with their functions and their type-definition
        # Give all objects and functions in all_objects the status 1 (which means pending)
        if len(self.mw.ct.pr.sel_fsmri) * len(self.sel_fsmri_funcs) != 0:
            for fsmri in self.mw.ct.pr.sel_fsmri:
                self.all_objects[fsmri] = {'type': 'FSMRI',
                                           'functions': {x: 1 for x in self.sel_fsmri_funcs},
                                           'status': 1}
                for fsmri_func in self.sel_fsmri_funcs:
                    self.all_steps.append((fsmri, fsmri_func))

        if len(self.mw.ct.pr.sel_meeg) * len(self.sel_meeg_funcs) != 0:
            for meeg in self.mw.ct.pr.sel_meeg:
                self.all_objects[meeg] = {'type': 'MEEG',
                                          'functions': {x: 1 for x in self.sel_meeg_funcs},
                                          'status': 1}
                for meeg_func in self.sel_meeg_funcs:
                    self.all_steps.append((meeg, meeg_func))

        if len(self.mw.ct.pr.sel_groups) * len(self.sel_group_funcs) != 0:
            for group in self.mw.ct.pr.sel_groups:
                self.all_objects[group] = {'type': 'Group',
                                           'functions': {x: 1 for x in self.sel_group_funcs},
                                           'status': 1}
                for group_func in self.sel_group_funcs:
                    self.all_steps.append((group, group_func))

        if len(self.sel_other_funcs) != 0:
            # blank object-name for other functions
            self.all_objects[''] = {'type': 'Other',
                                    'functions': {x: 1 for x in self.sel_other_funcs},
                                    'status': 1}
            for other_func in self.sel_other_funcs:
                self.all_steps.append(('', other_func))

    def init_ui(self):
        layout = QVBoxLayout()

        view_layout = QGridLayout()
        view_layout.addWidget(QLabel('Objects: '), 0, 0)
        self.object_listview = QListView()
        self.object_model = RunModel(self.all_objects, mode='object')
        self.object_listview.setModel(self.object_model)
        self.object_listview.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        view_layout.addWidget(self.object_listview, 1, 0)

        view_layout.addWidget(QLabel('Functions: '), 0, 1)
        self.func_listview = QListView()
        self.func_model = RunModel(self.current_all_funcs, mode='func')
        self.func_listview.setModel(self.func_model)
        self.func_listview.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        view_layout.addWidget(self.func_listview, 1, 1)

        view_layout.addWidget(QLabel('Errors: '), 0, 2)
        self.error_widget = SimpleList(list())
        self.error_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        # Connect Signal from error_widget to function to enable inspecting the errors
        self.error_widget.currentChanged.connect(self.show_error)
        view_layout.addWidget(self.error_widget, 1, 2)

        layout.addLayout(view_layout)

        self.console_widget = MainConsoleWidget()
        layout.addWidget(self.console_widget)

        self.pgbar = QProgressBar()
        self.pgbar.setValue(0)
        self.pgbar.setMaximum(len(self.all_steps))
        layout.addWidget(self.pgbar)

        bt_layout = QHBoxLayout()

        self.continue_bt = QPushButton('Continue')
        self.continue_bt.setFont(QFont('AnyStyle', 14))
        self.continue_bt.setIcon(get_std_icon('SP_MediaPlay'))
        self.continue_bt.clicked.connect(self.start_thread)
        bt_layout.addWidget(self.continue_bt)

        self.pause_bt = QPushButton('Pause')
        self.pause_bt.setFont(QFont('AnyStyle', 14))
        self.pause_bt.setIcon(get_std_icon('SP_MediaPause'))
        self.pause_bt.clicked.connect(self.pause_funcs)
        bt_layout.addWidget(self.pause_bt)

        self.restart_bt = QPushButton('Restart')
        self.restart_bt.setFont(QFont('AnyStyle', 14))
        self.restart_bt.setIcon(get_std_icon('SP_BrowserReload'))
        self.restart_bt.clicked.connect(self.restart)
        bt_layout.addWidget(self.restart_bt)

        self.autoscroll_bt = QPushButton('Auto-Scroll')
        self.autoscroll_bt.setCheckable(True)
        self.autoscroll_bt.setChecked(True)
        self.autoscroll_bt.setIcon(get_std_icon('SP_DialogOkButton'))
        self.autoscroll_bt.clicked.connect(self.toggle_autoscroll)
        bt_layout.addWidget(self.autoscroll_bt)

        self.close_bt = QPushButton('Close')
        self.close_bt.setFont(QFont('AnyStyle', 14))
        self.close_bt.setIcon(get_std_icon('SP_MediaStop'))
        self.close_bt.clicked.connect(self.close)
        bt_layout.addWidget(self.close_bt)
        layout.addLayout(bt_layout)

        self.setLayout(layout)

    def mark_current_items(self, status):
        # Mark current object with status
        self.all_objects[self.current_object.name]['status'] = status
        self.object_model.layoutChanged.emit()
        # Scroll to current object
        self.object_listview.scrollTo(self.object_model.createIndex(
                list(self.all_objects.keys()).index(self.current_object.name), 0))

        # Mark current function with status
        self.all_objects[self.current_object.name]['functions'][self.current_func] = status
        self.func_model.layoutChanged.emit()
        # Scroll to current function
        self.func_listview.scrollTo(self.func_model.createIndex(
                list(self.all_objects[self.current_object.name]['functions'].keys()).index(self.current_func), 0))

    def start_thread(self):
        # Set paused to false
        self.paused = False
        # Enable/Disable Buttons
        self.continue_bt.setEnabled(False)
        self.pause_bt.setEnabled(True)
        self.restart_bt.setEnabled(False)
        self.close_bt.setEnabled(False)

        # Take first step of all_steps until there are no steps left
        if len(self.all_steps) > 0:
            # Getting information as encoded in init_lists
            self.current_step = self.all_steps.pop(0)
            object_name = self.current_step[0]
            self.current_type = self.all_objects[object_name]['type']

            # Load object if the preceding object is not the same
            if not self.current_object or self.current_object.name != object_name:
                # Print Headline for object
                self.console_widget.add_html(f'<br><h1>{object_name}</h1><br>')

                if self.current_type == 'FSMRI':
                    self.current_object = FSMRI(object_name, self.mw.ct)
                    self.loaded_fsmri = self.current_object

                elif self.current_type == 'MEEG':
                    # Avoid reloading of same MRI-Subject for multiple files (with the same MRI-Subject)
                    if object_name in self.mw.ct.pr.meeg_to_fsmri \
                            and self.loaded_fsmri \
                            and self.loaded_fsmri.name == self.mw.ct.pr.meeg_to_fsmri[object_name]:
                        self.current_object = MEEG(object_name, self.mw.ct, fsmri=self.loaded_fsmri)
                    else:
                        self.current_object = MEEG(object_name, self.mw.ct)
                    self.loaded_fsmri = self.current_object.fsmri

                elif self.current_type == 'Group':
                    self.current_object = Group(object_name, self.mw.ct)

                elif self.current_type == 'Other':
                    self.current_object = BaseLoading(object_name, self.mw.ct)

                # Load functions for object into func_model (which displays functions in func_listview)
                self.current_all_funcs = self.all_objects[object_name]['functions']
                self.func_model._data = self.current_all_funcs
                self.func_model.layoutChanged.emit()

            self.current_func = self.current_step[1]

            # Mark current object and current function
            self.mark_current_items(2)

            # Print Headline for function
            self.console_widget.add_html(f'<h2>{self.current_func}</h2><br>')

            if (self.mw.ct.pd_funcs.loc[self.current_func, 'mayavi']
                    or self.mw.ct.pd_funcs.loc[self.current_func, 'matplotlib'] and self.mw.ct.get_setting('show_plots')):
                # Plot functions with interactive plots currently can't run in a separate thread
                try:
                    func_from_def(self.current_func, self.current_object)
                except:
                    exc_tuple = get_exception_tuple()
                    self.thread_error(exc_tuple)
                else:
                    self.thread_finished(None)
            else:
                self.fworker = Worker(function=func_from_def,
                                      func_name=self.current_func, obj=self.current_object)
                self.fworker.signals.error.connect(self.thread_error)
                self.fworker.signals.finished.connect(self.thread_finished)
                QThreadPool.globalInstance().start(self.fworker)

        else:
            self.console_widget.add_html('<b><big>Finished</big></b><br>')
            # Enable/Disable Buttons
            self.continue_bt.setEnabled(False)
            self.pause_bt.setEnabled(False)
            self.restart_bt.setEnabled(True)
            self.close_bt.setEnabled(True)

            if self.mw.ct.get_setting('shutdown'):
                self.mw.ct.save()
                shutdown()

    def thread_finished(self, _):
        self.prog_count += 1
        self.pgbar.setValue(self.prog_count)
        self.mark_current_items(0)

        # Close all plots if not wanted
        if not self.mw.ct.get_setting('show_plots'):
            close_all()

        # Collect Garbage to free memory
        gc.collect()

        if not self.paused:
            self.start_thread()
        else:
            self.console_widget.add_html('<b><big>Paused</big></b><br>')
            # Enable/Disable Buttons
            self.continue_bt.setEnabled(True)
            self.pause_bt.setEnabled(False)
            self.restart_bt.setEnabled(True)
            self.close_bt.setEnabled(True)

    def thread_error(self, err):
        error_cause = f'{self.error_count}: {self.current_object.name} <- {self.current_func}'
        self.errors[error_cause] = (err, self.error_count)
        # Update Error-Widget
        self.error_widget.replace_data(list(self.errors.keys()))

        # Insert Error-Number into console-widget as an anchor for later inspection
        self.console_widget.add_html(f'<a name=\"{self.error_count}\" href={self.error_count}>'
                                     f'<i>Error No.{self.error_count}</i><br></a>')
        # Increase Error-Count by one
        self.error_count += 1
        # Continue with next object
        self.thread_finished(None)

    def pause_funcs(self):
        self.paused = True
        self.console_widget.add_html('<br><b>Finishing last function...</b><br>')

    def restart(self):
        # Reload modules to get latest changes
        self.mw.ct.reload_modules()

        self.init_attributes()
        self.init_lists()

        # Clear Console-Widget
        self.console_widget.clear()

        # Redo References to display-widgets
        self.object_model._data = self.all_objects
        self.object_model.layoutChanged.emit()
        self.func_model._data = self.current_all_funcs
        self.func_model.layoutChanged.emit()
        self.error_widget.replace_data(list(self.errors.keys()))

        # Reset Progress-Bar
        self.pgbar.setValue(0)

        # Restart
        self.start_thread()

    def toggle_autoscroll(self, state):
        if state:
            self.console_widget.set_autoscroll(True)
        else:
            self.console_widget.set_autoscroll(False)

    def show_error(self, current, _):
        self.console_widget.set_autoscroll(False)
        self.autoscroll_bt.setChecked(False)
        self.console_widget.scrollToAnchor(str(self.errors[current][1]))

    def closeEvent(self, event):
        self.mw.pipeline_running = False
        event.accept()

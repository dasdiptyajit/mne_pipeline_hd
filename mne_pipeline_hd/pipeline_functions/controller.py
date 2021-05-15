import json
import os
import re
import sys
from os import listdir
from os.path import isdir, isfile, join

import mne
import pandas as pd
from importlib import reload, resources, util

from PyQt5.QtCore import QSettings
from PyQt5.QtWidgets import QInputDialog, QMessageBox

from mne_pipeline_hd import basic_functions
from mne_pipeline_hd.gui.gui_utils import ErrorDialog, get_exception_tuple
from mne_pipeline_hd.pipeline_functions.project import Project

home_dirs = ['custom_packages', 'freesurfer', 'projects']
project_dirs = ['_pipeline_scripts', 'data', 'figures']


class Controller:

    def __init__(self, home_path=None, current_project=None, edu_program_name=None):
        # Check Home-Path
        self.errors = dict()

        # Try to load home-path from QSettings
        if home_path is None:
            home_path = QSettings().value('home_path', defaultValue=None)
            if home_path is None:
                self.errors['home-path'] = f'No Home-Path found!'

        # Check if path exists
        if not isdir(home_path):
            self.errors['home-path'] = f'{home_path} not found!'

        # Check, if path is writable
        elif not os.access(home_path, os.W_OK):
            self.errors['home-path'] = f'{home_path} not writable!'

        else:
            self.home_path = home_path
            QSettings().setValue('home_path', home_path)
            # Create subdirectories if not existing for a valid home_path
            for subdir in [d for d in home_dirs if not isdir(join(home_path, d))]:
                os.mkdir(join(home_path, subdir))

            # Get Project-Folders (recognized by distinct sub-folders)
            self.projects_path = join(home_path, 'projects')
            self.projects = [p for p in listdir(self.projects_path)
                             if all([isdir(join(self.projects_path, p, d))
                                     for d in project_dirs])]

            # Initialize Subjects-Dir
            self.subjects_dir = join(self.home_path, 'freesurfer')
            mne.utils.set_config("SUBJECTS_DIR", self.subjects_dir, set_env=True)

            # Initialize folder for custom packages
            self.custom_pkg_path = join(self.home_path, 'custom_packages')

            # Initialize educational programs
            self.edu_program_name = edu_program_name
            self.edu_program = None

            # Load default settings
            with resources.open_text('mne_pipeline_hd.pipeline_resources',
                                     'default_settings.json') as file:
                self.default_settings = json.load(file)

            # Load settings (which are stored as .json-file in home_path)
            # settings=<everything, that's OS-independent>
            self.settings = dict()
            self.load_settings()

            self.all_modules = dict()
            self.all_pd_funcs = None

            # Pandas-DataFrame for contextual data of basic functions (included with program)
            with resources.path('mne_pipeline_hd.pipeline_resources',
                                'functions.csv') as pd_funcs_path:
                self.pd_funcs = pd.read_csv(str(pd_funcs_path), sep=';', index_col=0)

            # Pandas-DataFrame for contextual data of parameters
            # for basic functions (included with program)
            with resources.path('mne_pipeline_hd.pipeline_resources',
                                'parameters.csv') as pd_params_path:
                self.pd_params = pd.read_csv(str(pd_params_path), sep=';', index_col=0)

            # Import the basic- and custom-function-modules
            self.import_custom_modules()

            # Check Project
            if current_project is None:
                # Load settings to get current_project
                settings_path = join(home_path, 'mne_pipeline_hd-settings.json')
                if isfile(settings_path):
                    with open(settings_path, 'r') as file:
                        settings = json.load(file)
                        if 'current_project' in settings:
                            current_project = settings['current_project']

            if len(self.projects) == 0:
                self.errors['project'] = 'No projects!'

            elif current_project not in self.projects:
                self.errors['project'] = f'{current_project} not in projects!'

            else:
                self.current_project = current_project

            # Initialize Project
            self.pr = Project(self, self.current_project)

    def load_settings(self):
        try:
            with open(join(self.home_path,
                           'mne_pipeline_hd-settings.json'), 'r') as file:
                self.settings = json.load(file)
            # Account for settings, which were not saved but exist in default_settings
            for setting in [s for s in self.default_settings['settings']
                            if s not in self.settings]:
                self.settings[setting] = self.default_settings['settings'][setting]
        except FileNotFoundError:
            self.settings = self.default_settings['settings']

        # Check integrity of QSettings-Keys
        QSettings().sync()
        qs = set(QSettings().childKeys())
        ds = set(self.default_settings['qsettings'])
        # Remove additional (old) QSettings not appearing in default-settings
        for qsetting in qs - ds:
            QSettings().remove(qsetting)
        # Add new settings from default-settings which are not present in QSettings
        for qsetting in ds - qs:
            QSettings().setValue(qsetting, self.default_settings['qsettings'][qsetting])

    def save_settings(self):
        with open(join(self.home_path, 'mne_pipeline_hd-settings.json'), 'w') as file:
            json.dump(self.settings, file, indent=4)

        # Sync QSettings with other instances
        QSettings().sync()

    def get_setting(self, setting):
        try:
            value = self.settings[setting]
        except KeyError:
            value = self.default_settings['settings'][setting]

        return value

    def change_project(self, new_project):
        self.pr = Project(self, new_project)
        self.current_project = new_project
        self.settings['current_project'] = new_project
        if new_project not in self.projects:
            self.projects.append(new_project)

    def save(self, worker_signals):
        if worker_signals is not None:
            worker_signals.pgbar_text.emit('Saving Project...')

        # Save Project
        self.pr.save(worker_signals)

        if worker_signals is not None:
            worker_signals.pgbar_text.emit('Saving Settings...')

        self.settings['current_project'] = self.current_project
        self.save_settings()

    def load_edu(self):
        if self.edu_program_name:
            edu_path = join(self.home_path, 'edu_programs', self.edu_program_name)
            with open(edu_path, 'r') as file:
                self.edu_program = json.load(file)

            self.all_pd_funcs = self.pd_funcs.copy()
            # Exclude functions which are not selected
            self.pd_funcs = self.pd_funcs.loc[self.pd_funcs.index.isin(self.edu_program['functions'])]

            # Change the Project-Scripts-Path to a new folder to store the Education-Project-Scripts separately
            self.pr.pscripts_path = join(self.pr.project_path, f'_pipeline_scripts{self.edu_program["name"]}')
            if not isdir(self.pr.pscripts_path):
                os.mkdir(self.pr.pscripts_path)
            self.pr.init_pipeline_scripts()

            # Exclude MEEG
            self.pr._all_meeg = self.pr.all_meeg.copy()
            self.pr.all_meeg = [meeg for meeg in self.pr.all_meeg if meeg in self.edu_program['meeg']]

            # Exclude FSMRI
            self.pr._all_fsmri = self.pr.all_fsmri.copy()
            self.pr.all_fsmri = [meeg for meeg in self.pr.all_meeg if meeg in self.edu_program['meeg']]

    def import_custom_modules(self):
        """
        Load all modules in basic_functions and custom_functions
        """

        self.errors['custom-modules'] = dict()

        # Load basic-modules
        basic_functions_list = [x for x in dir(basic_functions) if '__' not in x]
        self.all_modules['basic'] = dict()
        for module_name in basic_functions_list:
            self.all_modules['basic'][module_name] = (getattr(basic_functions, module_name), None)

        # Load custom_modules
        pd_functions_pattern = r'.*_functions\.csv'
        pd_parameters_pattern = r'.*_parameters\.csv'
        custom_module_pattern = r'(.+)(\.py)$'
        for directory in [d for d in os.scandir(self.custom_pkg_path) if not d.name.startswith('.')]:
            pkg_name = directory.name
            pkg_path = directory.path
            file_dict = {'functions': None, 'parameters': None, 'modules': list()}
            for file_name in [f for f in listdir(pkg_path) if not f.startswith(('.', '_'))]:
                functions_match = re.match(pd_functions_pattern, file_name)
                parameters_match = re.match(pd_parameters_pattern, file_name)
                custom_module_match = re.match(custom_module_pattern, file_name)
                if functions_match:
                    file_dict['functions'] = join(pkg_path, file_name)
                elif parameters_match:
                    file_dict['parameters'] = join(pkg_path, file_name)
                elif custom_module_match and custom_module_match.group(1) != '__init__':
                    file_dict['modules'].append(custom_module_match)

            # Check, that there is a whole set for a custom-module (module-file, functions, parameters)
            if all([value is not None or value != [] for value in file_dict.values()]):
                self.all_modules[pkg_name] = dict()
                functions_path = file_dict['functions']
                parameters_path = file_dict['parameters']
                correct_count = 0
                for module_match in file_dict['modules']:
                    module_name = module_match.group(1)
                    module_file_name = module_match.group()

                    spec = util.spec_from_file_location(module_name, join(pkg_path, module_file_name))
                    module = util.module_from_spec(spec)
                    try:
                        spec.loader.exec_module(module)
                    except:
                        exc_tuple = get_exception_tuple()
                        self.errors['custom-modules'][module_name] = exc_tuple
                    else:
                        correct_count += 1
                        # Add module to sys.modules
                        sys.modules[module_name] = module
                        # Add Module to dictionary
                        self.all_modules[pkg_name][module_name] = (module, spec)

                # Make sure, that every module in modules is imported without error
                # (otherwise don't append to pd_funcs and pd_params)
                if len(file_dict['modules']) == correct_count:
                    try:
                        read_pd_funcs = pd.read_csv(functions_path, sep=';', index_col=0)
                        read_pd_params = pd.read_csv(parameters_path, sep=';', index_col=0)
                    except:
                        exc_tuple = get_exception_tuple()
                        self.errors['custom-modules'][pkg_name] = exc_tuple
                    else:
                        # Add pkg_name here (would be redundant in read_pd_funcs of each custom-package)
                        read_pd_funcs['pkg_name'] = pkg_name

                        # Check, that there are no duplicates
                        pd_funcs_to_append = read_pd_funcs.loc[~read_pd_funcs.index.isin(self.pd_funcs.index)]
                        self.pd_funcs = self.pd_funcs.append(pd_funcs_to_append)
                        pd_params_to_append = read_pd_params.loc[~read_pd_params.index.isin(self.pd_params.index)]
                        self.pd_params = self.pd_params.append(pd_params_to_append)

            else:
                error_text = f'Files for import of {pkg_name} are missing: ' \
                             f'{[key for key in file_dict if file_dict[key] is None]}'
                self.errors['custom-modules'][pkg_name] = error_text

        self.fsmri_funcs = self.pd_funcs[self.pd_funcs['target'] == 'FSMRI']
        self.meeg_funcs = self.pd_funcs[self.pd_funcs['target'] == 'MEEG']
        self.group_funcs = self.pd_funcs[self.pd_funcs['target'] == 'Group']
        self.other_funcs = self.pd_funcs[self.pd_funcs['target'] == 'Other']

    def reload_modules(self):
        for pkg_name in self.all_modules:
            for module_name in self.all_modules[pkg_name]:
                module = self.all_modules[pkg_name][module_name][0]
                try:
                    reload(module)
                # Custom-Modules somehow can't be reloaded because spec is not found
                except ModuleNotFoundError:
                    spec = self.all_modules[pkg_name][module_name][1]
                    if spec:
                        # All errors occuring here will be caught by the UncaughtHook
                        spec.loader.exec_module(module)
                        sys.modules[module_name] = module
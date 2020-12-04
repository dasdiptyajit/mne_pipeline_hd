# -*- coding: utf-8 -*-
"""
Pipeline-GUI for Analysis with MNE-Python
Copyright © 2011-2019, authors of MNE-Python (https://doi.org/10.3389/fnins.2013.00267)
inspired by Andersen, L. M. (2018) (https://doi.org/10.3389/fnins.2018.00006)
@author: Martin Schulz
@email: dev@earthman-music.de
@github: https://github.com/marsipu/mne_pipeline_hd
License: BSD (3-clause)
"""
from __future__ import print_function

import inspect
import pickle
from datetime import datetime
from os import listdir, makedirs, mkdir, remove
from os.path import exists, getsize, isdir, isfile, join
from pathlib import Path

import mne
import numpy as np
from PyQt5.QtWidgets import QMessageBox


# ==============================================================================
# LOADING FUNCTIONS
# ==============================================================================
class BaseLoading:
    """ Base-Class for Sub (The current File/MRI-File/Grand-Average-Group, which is executed)"""

    def __init__(self, name, main_win):
        # Basic Attributes (partly taking parameters or main-win-attributes for easier access)
        self.name = name
        self.mw = main_win
        self.pr = main_win.pr
        self.p_preset = self.pr.p_preset
        self.p = main_win.pr.parameters[self.p_preset]
        self.subjects_dir = self.mw.subjects_dir
        self.save_plots = self.mw.get_setting('save_plots')
        self.figures_path = self.pr.figures_path
        self.img_format = self.mw.get_setting('img_format')
        self.dpi = self.mw.get_setting('dpi')

        self.save_dir = None
        self.paths_dict = dict()
        self.methods_dict = dict()
        self.existing_paths = dict()

    def save_file_params(self, path):
        file_name = Path(path).name
        self.pr.file_parameters[file_name] = dict()
        # Get the name of the calling function (assuming it is 2 Frames above)
        self.pr.file_parameters[file_name]['FUNCTION'] = inspect.stack()[2][3]
        self.pr.file_parameters[file_name]['NAME'] = self.name
        self.pr.file_parameters[file_name]['PATH'] = path
        self.pr.file_parameters[file_name]['TIME'] = datetime.now()
        self.pr.file_parameters[file_name]['SIZE'] = getsize(path)
        for p_name in self.pr.parameters[self.p_preset]:
            self.pr.file_parameters[file_name][p_name] = self.pr.parameters[self.p_preset][p_name]

    def get_existing_paths(self):
        """Get existing paths and add the mapped File-Type to existing_paths (set)"""
        self.existing_paths.clear()
        for path_type in self.paths_dict:
            path = self.paths_dict[path_type]
            if isinstance(path, str) and isfile(path):
                self.existing_paths[path_type] = [path]
            elif isinstance(path, list):
                exst_paths = [p for p in path if isfile(p)]
                if len(exst_paths) > 0:
                    self.existing_paths[path_type] = exst_paths
            elif isinstance(path, dict):
                exst_paths = [p for p in path.values() if isfile(p)]
                if len(exst_paths) > 0:
                    self.existing_paths[path_type] = exst_paths

    def remove_path(self, path_type):
        # Remove path specified by path_type (which is the name mapped to the path in self.paths_dict)
        # Dependent on Paramter-Preset
        path = self.paths_dict[path_type]
        remove_list = list()
        if isinstance(path, str) and isfile(path):
            remove_list.append(path)
        elif isinstance(path, list):
            for path_item in [p for p in path if isfile(p)]:
                remove_list.append(path_item)
        elif isinstance(path, dict):
            for path_value in [p for p in path.values() if isfile(p)]:
                remove_list.append(path_value)

        for p in remove_list:
            try:
                remove(p)
                print(f'{p} was removed')
            except (IsADirectoryError, OSError) as err:
                print(f'{p} could not be removed due to {err}')


class MEEG(BaseLoading):
    """ Class for File-Data in File-Loop"""

    def __init__(self, name, main_win, fsmri=None, suppress_msgbx=True):

        super().__init__(name, main_win)

        # Additional Attributes
        self.save_dir = join(self.pr.data_path, name)

        # Attributes, which are needed to run the subject
        try:
            self.erm = self.mw.pr.meeg_to_erm[name]
        except KeyError:
            self.erm = 'None'
            warning = f'No Empty-Room-Measurement assigned for {self.name}, defaulting to "None"'
            print(warning)
            if not suppress_msgbx:
                QMessageBox.warning(self.mw, 'No ERM', warning)
        try:
            if fsmri and fsmri.name == self.mw.pr.meeg_to_fsmri[name]:
                self.fsmri = fsmri
            else:
                self.fsmri = FSMRI(self.mw.pr.meeg_to_fsmri[name], main_win)
        except KeyError:
            self.fsmri = FSMRI('None', main_win)
            warning = f'No MRI-Subject assigned for {self.name}, defaulting to "None"'
            print(warning)
            if not suppress_msgbx:
                QMessageBox.warning(self.mw, 'No MRI', warning)
        try:
            self.bad_channels = self.mw.pr.meeg_bad_channels[name]
        except KeyError:
            self.bad_channels = list()
            warning = f'No bad channels assigned for {self.name}, defaulting to empty list'
            print(warning)
            if not suppress_msgbx:
                QMessageBox.warning(self.mw, 'No Bad Channels', warning)
        try:
            self.event_id = self.mw.pr.meeg_event_id[name]
            if len(self.event_id) == 0:
                raise RuntimeError(name)
        except (KeyError, RuntimeError):
            self.event_id = dict()
            warning = f'No EventID assigned for {self.name}, defaulting to empty dictionary'
            print(warning)
            if not suppress_msgbx:
                QMessageBox.warning(self.mw, 'No Event-ID', warning)
        try:
            self.sel_trials = self.mw.pr.sel_event_id[name]
            if len(self.sel_trials) == 0:
                raise RuntimeError(name)
        except (KeyError, RuntimeError):
            self.sel_trials = list()
            warning = f'No Trials selected for {self.name}, defaulting to empty list'
            print(warning)
            if not suppress_msgbx:
                QMessageBox.warning(self.mw, 'No Trials', warning)

        self.load_attributes()
        self.load_paths()

    def load_attributes(self):
        """Set all Data-Attributes to None"""
        # Data-Attributes (not to be called directly)
        # Make Data-Objects usable by consecutive functions without reloading
        self._info = None
        self._raw = None
        self._raw_filtered = None
        self._erm_filtered = None
        self._events = None
        self._epochs = None
        self._reject_log = None
        self._ica = None
        self._ica_epochs = None
        self._evokeds = None
        self._power_tfr = None
        self._itc_tfr = None

        self._trans = None
        self._forward = None
        self._noise_cov = None
        self._inverse = None
        self._stcs = None
        self._ltc = None
        self._connectivity = None
        self._mixn_stcs = None
        self._mixn_dips = None
        self._ecd_dips = None

    def load_paths(self):
        """Load Paths as attributes (depending on which Parameter-Preset is selected)"""
        self.raw_path = join(self.save_dir, f'{self.name}-raw.fif')
        self.raw_filtered_path = join(self.save_dir, f'{self.name}_{self.p_preset}-filtered-raw.fif')

        self.erm_path = join(self.pr.erm_data_path, self.erm, f'{self.erm}-raw.fif')
        self.erm_filtered_path = join(self.pr.erm_data_path, self.erm, f'{self.erm}_{self.p_preset}-raw.fif')

        self.events_path = join(self.save_dir, f'{self.name}_{self.p_preset}-eve.fif')

        self.epochs_path = join(self.save_dir, f'{self.name}_{self.p_preset}-epo.fif')

        self.reject_log_path = join(self.save_dir, f'{self.name}_{self.p_preset}-arlog.py')

        self.ica_path = join(self.save_dir, f'{self.name}_{self.p_preset}-ica.fif')

        self.ica_epochs_path = join(self.save_dir, f'{self.name}_{self.p_preset}-ica-epo.fif')

        self.evokeds_path = join(self.save_dir, f'{self.name}_{self.p_preset}-ave.fif')

        self.power_tfr_path = join(self.save_dir, f'{self.name}_{self.p_preset}_{self.p["tfr_method"]}-pw-tfr.h5')
        self.itc_tfr_path = join(self.save_dir, f'{self.name}_{self.p_preset}_{self.p["tfr_method"]}-itc-tfr.h5')

        self.trans_path = join(self.save_dir, f'{self.fsmri.name}-trans.fif')

        self.forward_path = join(self.save_dir, f'{self.name}_{self.p_preset}-fwd.fif')

        self.calm_cov_path = join(self.save_dir, f'{self.name}_{self.p_preset}-calm-cov.fif')
        self.erm_cov_path = join(self.save_dir, f'{self.name}_{self.p_preset}-erm-cov.fif')
        self.cov_path = join(self.save_dir, f'{self.name}_{self.p_preset}-cov.fif')

        self.inverse_path = join(self.save_dir, f'{self.name}_{self.p_preset}-inv.fif')

        self.stc_paths = {trial: join(self.save_dir, f'{self.name}_{trial}_{self.p_preset}')
                          for trial in self.sel_trials}

        self.morphed_stc_paths = {trial: join(self.save_dir, f'{self.name}_{trial}_{self.p_preset}-morphed')
                                  for trial in self.sel_trials}

        self.paths_dict = {'Raw': self.raw_path,
                           'Raw (Filtered)': self.raw_filtered_path,
                           'EmptyRoom': self.erm_path,
                           'EmptyRoom (Filtered)': self.erm_filtered_path,
                           'Events': self.events_path,
                           'Epochs': self.epochs_path,
                           'RejectLog': self.reject_log_path,
                           'ICA': self.ica_path,
                           'Epochs (ICA)': self.ica_epochs_path,
                           'Evokeds': self.evokeds_path,
                           'TF Power': self.power_tfr_path,
                           'TF ITC': self.itc_tfr_path,
                           'Transformation': self.trans_path,
                           'Forward Solution': self.forward_path,
                           'Noise-Covariance (Calm)': self.calm_cov_path,
                           'Noise-Covariance (ERM)': self.erm_cov_path,
                           'Noise-Covariance': self.cov_path,
                           'Inverse Operator': self.inverse_path,
                           'Source Estimate': self.stc_paths,
                           'Source Estimate (Morphed)': self.morphed_stc_paths}

        self.methods_dict = {'load_info': 'Raw',
                             'load_raw': 'Raw',
                             'load_filtered': 'Raw (Filtered)',
                             'load_erm': 'EmptyRoom',
                             'load_erm_filtered': 'EmptyRoom (Filtered)',
                             'load_events': 'Events',
                             'load_epochs': 'Epochs',
                             'load_reject_log': 'RejectLog',
                             'load_ica': 'ICA',
                             'load_ica_epohcs': 'Epochs (ICA)',
                             'load_evokeds': 'Evokeds',
                             'load_power_tfr': 'TF Power',
                             'load_itc_tfr': 'TF ITC',
                             'load_transformation': 'Transformation',
                             'load_forward': 'Forward Solution',
                             'load_noise_covariance': 'Noise Covariance',
                             'load_inverse_operator': 'Inverse Operator',
                             'load_source_estimates': 'Source Estimate',
                             'load_morphed_source_estimates': 'Source Estimate (Morphed)'}

    def update_file_data(self):
        self.erm = self.mw.pr.meeg_to_erm[self.name]
        self.fsmri = self.mw.pr.meeg_to_fsmri[self.name]
        self.bad_channels = self.mw.pr.meeg_bad_channels[self.name]
        self.events_id = self.mw.pr.meeg_event_id[self.name]
        self.sel_trials = self.mw.pr.sel_event_id[self.name]

    ####################################################################################################################
    # Load- & Save-Methods
    ####################################################################################################################
    def load_info(self):
        """Get raw-info, either from all_info in project or from raw-file if not in all_info"""
        if self._info is None:
            self._info = mne.io.read_info(self.raw_path)

        return self._info

    def pick_types_helper(self, data):
        # Include all selected Channel-Types if present in data
        sel_ch_types = self.p['ch_types'].copy()
        existing_ch_types = data.get_channel_types(unique=True)
        # Remove channel-types, which are not present in data
        for ch_type in [ct for ct in sel_ch_types if ct not in existing_ch_types]:
            sel_ch_types.remove(ch_type)

        data = data.pick(sel_ch_types)
        print(f'Picked Types: {sel_ch_types}')

        return data

    def _update_raw_info(self):
        # Insert/Update BadChannels from meeg_bad_channels and add description
        self._raw.info['bads'] = self.bad_channels
        self._raw.info['description'] = self.name

    def load_raw(self):
        if self._raw is None:
            self._raw = mne.io.read_raw_fif(self.raw_path, preload=True)

        self._raw = self.pick_types_helper(self._raw)
        self._update_raw_info()

        return self._raw

    def save_raw(self, raw):
        self._raw = raw

        self._update_raw_info()

        raw.save(self.raw_path, overwrite=True)
        self.save_file_params(self.raw_path)

    def load_filtered(self):
        if self._raw_filtered is None:
            self._raw_filtered = mne.io.read_raw_fif(self.raw_filtered_path, preload=True)

        self._raw_filtered = self.pick_types_helper(self._raw_filtered)

        self._update_raw_info()

        return self._raw_filtered

    def save_filtered(self, raw_filtered):
        self._raw_filtered = raw_filtered

        self._update_raw_info()

        if not self.mw.get_setting('save_storage'):
            raw_filtered.save(self.raw_filtered_path, overwrite=True)
            self.save_file_params(self.raw_filtered_path)

    def load_erm(self):
        # unfiltered erm is not considered important enough to be a obj-attribute
        erm = mne.io.read_raw_fif(self.erm_path, preload=True)
        erm = self.pick_types_helper(erm)
        return erm

    def load_erm_filtered(self):
        if self._erm_filtered is None:
            self._erm_filtered = mne.io.read_raw_fif(self.erm_filtered_path, preload=True)

        return self._erm_filtered

    def save_erm_filtered(self, erm_filtered):
        self._erm_filtered = erm_filtered
        if not self.mw.get_setting('save_storage'):
            self._erm_filtered.save(self.erm_filtered_path, overwrite=True)
            self.save_file_params(self.erm_filtered_path)

    def load_events(self):
        if self._events is None:
            self._events = mne.read_events(self.events_path)

        return self._events

    def save_events(self, events):
        self._events = events
        mne.event.write_events(self.events_path, events)
        self.save_file_params(self.events_path)

    def load_epochs(self):
        if self._epochs is None:
            self._epochs = mne.read_epochs(self.epochs_path)

        self._epochs = self.pick_types_helper(self._epochs)

        return self._epochs

    def save_epochs(self, epochs):
        self._epochs = epochs
        epochs.save(self.epochs_path, overwrite=True)
        self.save_file_params(self.epochs_path)

    def load_reject_log(self):
        if self._reject_log is None:
            with open(self.reject_log_path, 'rb') as file:
                self._reject_log = pickle.load(file)

        return self._reject_log

    def save_reject_log(self, reject_log):
        self._reject_log = reject_log
        with open(self.reject_log_path, 'wb') as file:
            pickle.dump(reject_log, file)
        self.save_file_params(self.reject_log_path)

    def load_ica(self):
        if self._ica is None:
            self._ica = mne.preprocessing.read_ica(self.ica_path)

        return self._ica

    def save_ica(self, ica):
        self._ica = ica
        ica.save(self.ica_path)
        self.save_file_params(self.ica_path)

    def load_ica_epochs(self):
        if self._ica_epochs is None:
            self._ica_epochs = mne.read_epochs(self.ica_epochs_path)

        return self._ica_epochs

    def save_ica_epochs(self, ica_epochs):
        self._ica_epochs = ica_epochs
        ica_epochs.save(self.ica_epochs_path, overwrite=True)
        self.save_file_params(self.ica_epochs_path)

    def load_evokeds(self):
        if self._evokeds is None:
            self._evokeds = mne.read_evokeds(self.evokeds_path)

        for idx, evoked in enumerate(self._evokeds):
            self._evokeds[idx] = self.pick_types_helper(evoked)

        return self._evokeds

    def save_evokeds(self, evokeds):
        self._evokeds = evokeds
        mne.evoked.write_evokeds(self.evokeds_path, evokeds)
        self.save_file_params(self.evokeds_path)

    def load_power_tfr(self):
        if self._power_tfr is None:
            self._power_tfr = mne.time_frequency.read_tfrs(self.power_tfr_path)

        return self._power_tfr

    def save_power_tfr(self, powers):
        self._power_tfr = powers
        mne.time_frequency.write_tfrs(self.power_tfr_path, powers, overwrite=True)
        self.save_file_params(self.power_tfr_path)

    def load_itc_tfr(self):
        if self._itc_tfr is None:
            self._itc_tfr = mne.time_frequency.read_tfrs(self.itc_tfr_path)

        return self._itc_tfr

    def save_itc_tfr(self, itcs):
        self._itc_tfr = itcs
        mne.time_frequency.write_tfrs(self.itc_tfr_path, itcs, overwrite=True)
        self.save_file_params(self.itc_tfr_path)

    # Source-Space
    def load_transformation(self):
        if self._trans is None:
            self._trans = mne.read_trans(self.trans_path)

        return self._trans

    def load_forward(self):
        if self._forward is None:
            self._forward = mne.read_forward_solution(self.forward_path, verbose='WARNING')

        return self._forward

    def save_forward(self, forward):
        self._forward = forward
        mne.write_forward_solution(self.forward_path, forward, overwrite=True)
        self.save_file_params(self.forward_path)

    def load_noise_covariance(self):
        if self._noise_cov is None:
            if self.p['calm_noise_cov']:
                self._noise_cov = mne.read_cov(self.calm_cov_path)
                print('Reading Noise-Covariance from 1-min Calm in raw')

            elif self.erm == 'None' or self.p['erm_noise_cov'] is False:
                self._noise_cov = mne.read_cov(self.cov_path)
                print('Reading Noise-Covariance from Epochs')
            else:
                self._noise_cov = mne.read_cov(self.erm_cov_path)
                print('Reading Noise-Covariance from Empty-Room-Data')

        return self._noise_cov

    def save_noise_covariance(self, noise_cov, cov_type):
        self._noise_cov = noise_cov
        if cov_type == 'calm':
            mne.cov.write_cov(self.calm_cov_path, noise_cov)
            self.save_file_params(self.calm_cov_path)
        elif cov_type == 'epochs':
            mne.cov.write_cov(self.cov_path, noise_cov)
            self.save_file_params(self.cov_path)
        elif cov_type == 'erm':
            mne.cov.write_cov(self.erm_cov_path, noise_cov)
            self.save_file_params(self.erm_cov_path)

    def load_inverse_operator(self):
        if self._inverse is None:
            self._inverse = mne.minimum_norm.read_inverse_operator(self.inverse_path, verbose='WARNING')

        return self._inverse

    def save_inverse_operator(self, inverse):
        self._inverse = inverse
        mne.minimum_norm.write_inverse_operator(self.inverse_path, inverse)
        self.save_file_params(self.inverse_path)

    def load_source_estimates(self):
        if self._stcs is None:
            self._stcs = dict()
            for trial in self.stc_paths:
                stc = mne.source_estimate.read_source_estimate(self.stc_paths[trial])
                self._stcs[trial] = stc

        return self._stcs

    def save_source_estimates(self, stcs):
        self._stcs = stcs
        for trial in stcs:
            try:
                stcs[trial].save(self.stc_paths[trial])
                self.save_file_params(self.stc_paths[trial])
            except KeyError:
                raise RuntimeError(f'Selected Trials{list(self.stc_paths.keys())} don\'t seem to match {trial}'
                                   f'in saved source-estimate')

    def load_morphed_source_estimates(self):
        if self._morphed_stcs is None:
            self._morphed_stcs = dict()
            for trial in self.morphed_stc_paths:
                morphed_stc = mne.source_estimate.read_source_estimate(self.morphed_stc_paths[trial])
                self._morphed_stcs[trial] = morphed_stc

    def save_morphed_source_estimates(self, morphed_stcs):
        self._morphed_stcs = morphed_stcs
        for trial in morphed_stcs:
            try:
                morphed_stcs[trial].save(self.morphed_stc_paths[trial])
                self.save_file_params(self.morphed_stc_paths[trial])
            except KeyError:
                raise RuntimeError(f'Selected Trials{list(self.morphed_stc_paths.keys())} don\'t seem to match {trial}'
                                   f'in saved morphed source-estimate')

    def load_mixn_dipoles(self):
        if self._mixn_dips is None:
            self._mixn_dips = dict()
            for trial in self.sel_trials:
                idx = 0
                dip_list = list()
                for idx in range(len(listdir(join(self.save_dir, 'mixn_dipoles')))):
                    mixn_dip_path = join(self.save_dir, 'mixn_dipoles',
                                         f'{self.name}_{trial}_{self.p_preset}-mixn-dip{idx}.dip')
                    dip_list.append(mne.read_dipole(mixn_dip_path))
                    idx += 1
                self._mixn_dips[trial] = dip_list
                print(f'{idx + 1} dipoles read for {self.name}-{trial}')

        return self._mixn_dips

    def save_mixn_dipoles(self, mixn_dips):
        self._mixn_dips = mixn_dips

        # Remove old dipoles
        if not exists(join(self.save_dir, 'mixn_dipoles')):
            makedirs(join(self.save_dir, 'mixn_dipoles'))
        old_dipoles = listdir(join(self.save_dir, 'mixn_dipoles'))
        for file in old_dipoles:
            remove(join(self.save_dir, 'mixn_dipoles', file))

        for trial in mixn_dips:
            for idx, dip in enumerate(mixn_dips[trial]):
                mxn_dip_path = join(self.save_dir, 'mixn_dipoles',
                                    f'{self.name}_{trial}_{self.p_preset}-mixn-dip{idx}.dip')
                dip.save(mxn_dip_path)
                self.save_file_params(mxn_dip_path)

    def load_mixn_source_estimates(self):
        if self._mixn_stcs is None:
            self._mixn_stcs = dict()
            for trial in self.sel_trials:
                mx_stc_path = join(self.save_dir, f'{self.name}_{trial}_{self.p_preset}-mixn')
                mx_stc = mne.source_estimate.read_source_estimate(mx_stc_path)
                self._mixn_stcs.update({trial: mx_stc})

        return self._mixn_stcs

    def save_mixn_source_estimates(self, stcs):
        self._mixn_stcs = stcs
        for trial in stcs:
            stc_path = join(self.save_dir, f'{self.name}_{trial}_{self.p_preset}-mixn')
            stcs[trial].save(stc_path)
            self.save_file_params(stc_path)

    def load_ecd(self):
        if self._ecd_dips is None:
            self._ecd_dips = {}
            for trial in self.sel_trials:
                self._ecd_dips[trial] = {}
                for dip in self.p['ecd_times'][self.name]:
                    ecd_dip_path = join(self.save_dir, 'ecd_dipoles',
                                        f'{self.name}_{trial}_{self.p_preset}_{dip}-ecd-dip.dip')
                    self._ecd_dips[trial][dip] = mne.read_dipole(ecd_dip_path)
        return self._ecd_dips

    def save_ecd(self, ecd_dips):
        self._ecd_dips = ecd_dips
        if not exists(join(self.save_dir, 'ecd_dipoles')):
            makedirs(join(self.save_dir, 'ecd_dipoles'))

        for trial in ecd_dips:
            for dip in ecd_dips[trial]:
                ecd_dip_path = join(self.save_dir, 'ecd_dipoles',
                                    f'{self.name}_{trial}_{self.p_preset}_{dip}-ecd-dip.dip')
                ecd_dips[trial][dip].save(ecd_dip_path, overwrite=True)
                self.save_file_params(ecd_dip_path)

    def load_ltc(self):
        if self._ltc is None:
            self._ltc = {}
            for trial in self.sel_trials:
                self._ltc[trial] = {}
                for label in self.p['target_labels']:
                    ltc_path = join(self.save_dir, 'label_time_course',
                                    f'{self.name}_{trial}_{self.p_preset}_{label}.npy')
                    self._ltc[trial][label] = np.load(ltc_path)

        return self._ltc

    def save_ltc(self, ltc):
        self._ltc = ltc
        if not exists(join(self.save_dir, 'label_time_course')):
            makedirs(join(self.save_dir, 'label_time_course'))

        for trial in ltc:
            for label in ltc[trial]:
                ltc_path = join(self.save_dir, 'label_time_course',
                                f'{self.name}_{trial}_{self.p_preset}_{label}.npy')
                np.save(ltc_path, ltc[trial][label])
                self.save_file_params(ltc_path)

    def load_connectivity(self):
        if self._connectivity is None:
            self._connectivity = dict()
            for trial in self.sel_trials:
                self._connectivity[trial] = {}
                for con_method in self.p['con_methods']:
                    try:
                        con_path = join(self.save_dir, f'{self.name}_{trial}_{self.p_preset}_{con_method}.npy')
                        self._connectivity[trial][con_method] = np.load(con_path)
                    except FileNotFoundError:
                        pass

        return self._connectivity

    def save_connectivity(self, con_dict):
        self._connectivity = con_dict
        for trial in con_dict:
            for con_method in con_dict[trial]:
                con_path = join(self.save_dir, f'{self.name}_{trial}_{self.p_preset}_{con_method}.npy')
                np.save(con_path)
                self.save_file_params(con_path)


class FSMRI(BaseLoading):
    # Todo: Store available parcellations, surfaces, etc. (maybe already loaded with import?)
    def __init__(self, name, main_win):

        super().__init__(name, main_win)

        # Additional Attributes
        self.save_dir = join(self.mw.subjects_dir, self.name)
        self.fs_path = self.mw.qsettings.value('fs_path')
        self.mne_path = self.mw.qsettings.value('mne_path')

        self.load_attributes()
        self.load_paths()

    def load_attributes(self):
        """Set all Data-Attributes to None"""
        # Data-Attributes (not to be called directly)
        # Make Data-Objects usable by consecutive functions without reloading
        self._source_space = None
        self._bem_model = None
        self._bem_solution = None
        self._vol_source_space = None
        self._source_morph = None
        self._labels = None

    def load_paths(self):
        self.source_space_path = join(self.save_dir, 'bem', f'{self.name}_{self.p["source_space_spacing"]}-src.fif')
        self.bem_model_path = join(self.save_dir, 'bem', f'{self.name}-bem.fif')
        self.bem_solution_path = join(self.save_dir, 'bem', f'{self.name}-bem-sol.fif')
        self.vol_source_space_path = join(self.save_dir, 'bem', f'{self.name}-vol-src.fif')
        self.source_morph_path = join(self.save_dir,
                                      f'{self.name}--to--{self.p["morph_to"]}_'
                                      f'{self.p["source_space_spacing"]}-morph.h5')
        self.old_source_morph_path = join(self.save_dir,
                                          f'{self.name}--to--{self.p["morph_to"]}-'
                                          f'{self.p["source_space_spacing"]}-morph.h5')

        self.paths_dict = {'Source-Space': self.source_space_path,
                           'BEM-Model': self.bem_model_path,
                           'BEM-Solution': self.bem_solution_path,
                           'Volume-Source-Space': self.vol_source_space_path,
                           'Source-Morph': self.source_morph_path}

        self.methods_dict = {'load_source_space': 'Source-Space',
                             'load_bem_model': 'BEM-Model',
                             'load_bem_solution': 'BEM-Solution',
                             'load_vol_source_space': 'Volume-Source-Space',
                             'load_source_morph': 'Source-Morph'}

    ####################################################################################################################
    # Load- & Save-Methods
    ####################################################################################################################
    def load_source_space(self):
        if self._source_space is None:
            self._source_space = mne.source_space.read_source_spaces(self.source_space_path)

        return self._source_space

    def save_source_space(self, src):
        self._source_space = src
        src.save(self.source_space_path, overwrite=True)
        self.save_file_params(self.source_space_path)

    def load_bem_model(self):
        if self._bem_model is None:
            self._bem_model = mne.read_bem_surfaces(self.bem_model_path)

        return self._bem_model

    def save_bem_model(self, bem_model):
        self._bem_model = bem_model
        mne.write_bem_surfaces(self.bem_model_path, bem_model, overwrite=True)
        self.save_file_params(self.bem_model_path)

    def load_bem_solution(self):
        if self._bem_solution is None:
            self._bem_solution = mne.read_bem_solution(self.bem_solution_path)

        return self._bem_solution

    def save_bem_solution(self, bem_solution):
        self._bem_solution = bem_solution
        mne.write_bem_solution(self.bem_solution_path, bem_solution, overwrite=True)
        self.save_file_params(self.bem_solution_path)

    def load_vol_source_space(self):
        if self._vol_source_space is None:
            self._vol_source_space = mne.source_space.read_source_spaces(self.vol_source_space_path)

        return self._vol_source_space

    def save_vol_source_space(self, vol_source_space):
        self._vol_source_space = vol_source_space
        vol_source_space.save(self.vol_source_space_path, overwrite=True)
        self.save_file_params(self.vol_source_space_path)

    def load_source_morph(self):
        if self._source_morph is None:
            try:
                self._source_morph = mne.read_source_morph(self.source_morph_path)
            except OSError:
                self._source_morph = mne.read_source_morph(self.old_source_morph_path)

        return self._source_morph

    def save_source_morph(self, source_morph):
        self._source_morph = source_morph
        source_morph.save(self.source_morph_path, overwrite=True)
        self.save_file_params(self.source_morph_path)

    def load_parc_labels(self):
        if self._labels is None:
            self._labels = mne.read_labels_from_annot(self.name, parc=self.p['parcellation'],
                                                      subjects_dir=self.subjects_dir)
        return self._labels


class Group(BaseLoading):
    def __init__(self, name, main_win, suppress_warnings=True):

        super().__init__(name, main_win)

        # Additional Attributes
        self.save_dir = self.pr.save_dir_averages
        self.group_list = self.pr.all_groups[name]

        try:
            self.event_id = self.mw.pr.meeg_event_id[self.group_list[0]]
            if len(self.event_id) == 0:
                raise RuntimeError(name)
        except (KeyError, RuntimeError):
            self.event_id = dict()
            if not suppress_warnings:
                QMessageBox.warning(self.mw, 'No Event-ID',
                                    f'No EventID assigned for {self.name}, defaulting to empty dictionary')
        try:
            self.sel_trials = self.mw.pr.sel_event_id[self.group_list[0]]
            if len(self.sel_trials) == 0:
                raise RuntimeError(name)
        except (KeyError, RuntimeError):
            self.sel_trials = list()
            if not suppress_warnings:
                QMessageBox.warning(self.mw, 'No Trials',
                                    f'No Trials selected for {self.name}, defaulting to empty list')

        self.load_attributes()
        self.load_paths()

    def load_attributes(self):
        """Set all Data-Attributes to None"""
        # Data-Attributes (not to be called directly)
        # Make Data-Objects usable by consecutive functions without reloading
        self._ga_evokeds = None
        self._ga_tfr = None
        self._ga_stcs = None
        self._ga_ltc = None
        self._ga_connect = None

    def load_paths(self):
        self.ga_evokeds_folder = join(self.save_dir, 'evokeds')
        self.ga_evokeds_path = join(self.ga_evokeds_folder, f'{self.name}_{self.p_preset}-ave.fif')

        self.ga_tfr_folder = join(self.save_dir, 'time-frequency')

        self.ga_stc_folder = join(self.save_dir, 'source-estimate')

        self.ga_ltc_folder = join(self.save_dir, 'label-time-course')

        self.ga_con_folder = join(self.save_dir, 'connectivity')

        group_folders = [self.ga_evokeds_folder, self.ga_tfr_folder, self.ga_stc_folder, self.ga_ltc_folder,
                         self.ga_con_folder]
        for folder in [f for f in group_folders if not isdir(f)]:
            mkdir(folder)

        self.paths_dict = {'Grand-Average Evokeds': self.ga_evokeds_path}

        self.methods_dict = {'load_ga_evokeds': 'Grand-Average Evokeds'}

    ####################################################################################################################
    # Load- & Save-Methods
    ####################################################################################################################
    def load_ga_evokeds(self):
        if self._ga_evokeds is None:
            self._ga_evokeds = mne.read_evokeds(self.ga_evokeds_path)

        return self._ga_evokeds

    def save_ga_evokeds(self, ga_evokeds):
        self._ga_evokeds = ga_evokeds
        mne.evoked.write_evokeds(self.ga_evokeds_path, ga_evokeds)
        self.save_file_params(self.ga_evokeds_path)

    def load_ga_tfr(self):
        if self._ga_tfr is None:
            self._ga_tfr = {}
            for trial in self.sel_trials:
                ga_path = join(self.ga_tfr_folder,
                               f'{self.name}_{trial}_{self.p_preset}_{self.p["tfr_method"]}-tfr.h5')
                power = mne.time_frequency.read_tfrs(ga_path)[0]
                self._ga_tfr[trial] = power

        return self._ga_tfr

    def save_ga_tfr(self, ga_tfr, trial):
        ga_path = join(self.ga_tfr_folder,
                       f'{self.name}_{trial}_{self.p_preset}_{self.p["tfr_method"]}-tfr.h5')
        ga_tfr.save(ga_path)
        self.save_file_params(ga_path)

    def load_ga_source_estimate(self):
        if self._ga_stcs is None:
            self._ga_stcs = {}
            for trial in self.sel_trials:
                ga_stc_path = join(self.ga_stc_folder,
                                   f'{self.name}_{trial}_{self.p_preset}')
                self._ga_stcs[trial] = mne.read_source_estimate(ga_stc_path)

        return self._ga_stcs

    def save_ga_source_estimate(self, ga_stcs):
        self._ga_stcs = ga_stcs
        for trial in ga_stcs:
            ga_stc_path = join(self.ga_stc_folder,
                               f'{self.name}_{trial}_{self.p_preset}')
            ga_stcs[trial].save(ga_stc_path)
            self.save_file_params(ga_stc_path)

    def load_ga_ltc(self):
        if self._ga_ltc is None:
            self._ga_ltc = {}
            for trial in self.sel_trials:
                self._ga_ltc[trial] = {}
                for label in self.p['target_labels']:
                    ga_ltc_path = join(self.ga_ltc_folder,
                                       f'{self.name}_{trial}_{self.p_preset}_{label}.npy')
                    try:
                        self._ga_ltc[trial][label] = np.load(ga_ltc_path)
                    except FileNotFoundError:
                        print(f'{ga_ltc_path} not found')

        return self._ga_ltc

    def save_ga_ltc(self, ga_ltc):
        self._ga_ltc = ga_ltc
        for trial in ga_ltc:
            for label in ga_ltc[trial]:
                ga_ltc_path = join(self.ga_ltc_folder,
                                   f'{self.name}_{trial}_{self.p_preset}_{label}.npy')
                np.save(ga_ltc_path, ga_ltc[trial][label])
                self.save_file_params(ga_ltc_path)

    def load_ga_connect(self):
        if self._ga_connect is None:
            self._ga_connect = {}
            for trial in self.sel_trials:
                self._ga_connect[trial] = {}
                for con_method in self.p['con_methods']:
                    con_path = join(self.ga_con_folder,
                                    f'{self.name}_{trial}_{self.p_preset}_{con_method}.npy')
                    try:
                        self._ga_connect[trial][con_method] = np.load(con_path)
                    except FileNotFoundError:
                        print(f'{con_path} not found')

        return self._ga_connect

    def save_ga_connect(self, ga_con):
        self._ga_connect = ga_con
        for trial in ga_con:
            for con_method in ga_con[trial]:
                con_path = join(self.ga_con_folder,
                                f'{self.name}_{trial}_{self.p_preset}_{con_method}.npy')
                np.save(con_path, ga_con[trial][con_method])
                self.save_file_params(con_path)
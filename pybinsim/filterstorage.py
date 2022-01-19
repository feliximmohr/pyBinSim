# This file is part of the pyBinSim project.
#
# Copyright (c) 2017 A. Neidhardt, F. Klein, N. Knoop, T. Köllmer
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import logging
import multiprocessing as mp
import enum

import numpy as np
import soundfile as sf

import pyfftw
import time

from pybinsim.pose import Pose
from pybinsim.utility import total_size

from scipy.spatial import KDTree

nThreads = mp.cpu_count()


class Filter(object):

    def __init__(self, inputfilter, irBlocks, block_size, filename=None):
        self.log = logging.getLogger("pybinsim.Filter")

        self.ir_blocks = irBlocks
        self.block_size = block_size

        self.TF_blocks = irBlocks
        self.TF_block_size = block_size + 1
    
        self.IR_left_blocked = np.reshape(inputfilter[:, 0], (irBlocks, block_size))
        self.IR_right_blocked = np.reshape(inputfilter[:, 1], (irBlocks, block_size))
        self.filename = filename
        
        self.fd_available = False
        self.TF_left_blocked = None
        self.TF_right_blocked = None

    def getFilter(self):
        return self.IR_left_blocked, self.IR_right_blocked
    
    def getFilterTD(self):
        if self.fd_available:
            self.log.warning("FilterStorage: No time domain filter available!")
            left = np.zeros((self.ir_blocks, self.block_size))
            right = np.zeros((self.ir_blocks, self.block_size))
        else:
            left = self.IR_left_blocked
            right = self.IR_right_blocked

        return left, right

    def apply_fadeout(self,window):
        self.IR_left_blocked[self.ir_blocks-1, :] = np.multiply(self.IR_left_blocked[self.ir_blocks-1, :], window)
        self.IR_right_blocked[self.ir_blocks-1, :] = np.multiply(self.IR_right_blocked[self.ir_blocks-1, :], window)

    def apply_fadein(self,window):
        self.IR_left_blocked[0, :] = np.multiply(self.IR_left_blocked[0, :], window)
        self.IR_right_blocked[0, :] = np.multiply(self.IR_right_blocked[0, :], window)

    def storeInFDomain(self,fftw_plan):
        self.TF_left_blocked = np.zeros((self.ir_blocks, self.block_size + 1), dtype='complex64')
        self.TF_right_blocked = np.zeros((self.ir_blocks, self.block_size + 1), dtype='complex64')

        self.TF_left_blocked [:] = fftw_plan(self.IR_left_blocked)
        self.TF_right_blocked [:] = fftw_plan(self.IR_right_blocked)

        self.fd_available = True

        # Discard time domain data
        self.IR_left_blocked = None
        self.IR_right_blocked = None

    def getFilterFD(self):
        if not self.fd_available:
            self.log.warning("FilterStorage: No frequency domain filter available!")
            left = np.zeros((self.ir_blocks, self.block_size+1))
            right = np.zeros((self.ir_blocks, self.block_size+1))
        else:
            left = self.TF_left_blocked
            right = self.TF_right_blocked

        return left, right

class FilterType(enum.Enum):
    Undefined = 0
    Filter = 1
    LateReverbFilter = 2
    Directivity = 3

class FilterStorage(object):
    """ Class for storing all filters mentioned in the filter list """

    #def __init__(self, irSize, block_size, filter_list_name):
    def __init__(self, irSize, block_size, filter_list_name, useHeadphoneFilter = False, headphoneFilterSize = 0, useSplittedFilters = False, lateReverbSize = 0, directivitySize = 0):

        self.log = logging.getLogger("pybinsim.FilterStorage")
        self.log.info("FilterStorage: init")
        
        pyfftw.interfaces.cache.enable()
        fftw_planning_effort ='FFTW_ESTIMATE'

        self.ir_size = irSize
        self.ir_blocks = irSize // block_size
        self.block_size = block_size
        
        self.filter_fftw_plan = pyfftw.builders.rfft(np.zeros((self.ir_blocks,self.block_size), dtype='float32'),n=self.block_size*2,axis = 1, threads=nThreads, planner_effort=fftw_planning_effort)
        
        self.default_filter = Filter(np.zeros((self.ir_size, 2), dtype='float32'), self.ir_blocks, self.block_size)
        self.default_filter.storeInFDomain(self.filter_fftw_plan)
        
        # Calculate COSINE-Square crossfade windows
        self.crossFadeOut = np.array(range(0, self.block_size), dtype='float32')
        self.crossFadeOut = np.square(np.cos(self.crossFadeOut/(self.block_size-1)*(np.pi/2)))
        self.crossFadeIn = np.flipud(self.crossFadeOut)

        self.useHeadphoneFilter = useHeadphoneFilter
        if useHeadphoneFilter:
            self.headPhoneFilterSize = headphoneFilterSize
            self.headphone_ir_blocks = headphoneFilterSize // block_size

            self.hp_filter_fftw_plan = pyfftw.builders.rfft(np.zeros((self.headphone_ir_blocks, self.block_size), dtype='float32'),
                                                          n=self.block_size * 2, axis=1, overwrite_input=False,
                                                          threads=nThreads, planner_effort=fftw_planning_effort,
                                                          avoid_copy=False)

        self.useSplittedFilters = useSplittedFilters
        if useSplittedFilters:
            self.lateReverbSize = lateReverbSize
            self.late_ir_blocks = lateReverbSize // block_size

            self.late_filter_fftw_plan = pyfftw.builders.rfft(np.zeros((self.late_ir_blocks, self.block_size), dtype='float32'),
                                                         n=self.block_size * 2, axis=1, overwrite_input=False,
                                                         threads=nThreads, planner_effort=fftw_planning_effort,
                                                         avoid_copy=False)

            self.default_late_reverb_filter = Filter(np.zeros((self.lateReverbSize, 2), dtype='float32'), self.late_ir_blocks, self.block_size)
            self.default_late_reverb_filter.storeInFDomain(self.late_filter_fftw_plan)

        # TODO: Check if this is correct
        # directivity
        self.directivitySize = directivitySize
        self.dir_ir_blocks = directivitySize // block_size
        self.filter_fftw_plan = pyfftw.builders.rfft(np.zeros((self.dir_ir_blocks, self.block_size), dtype='float32'),
                                                     n=self.block_size * 2, axis=1, overwrite_input=False,
                                                     threads=nThreads, planner_effort=fftw_planning_effort,
                                                     avoid_copy=False)
        self.default_directivity_filter = Filter(np.zeros((self.directivitySize, 2), dtype='float32'), self.dir_ir_blocks, self.block_size)
        self.default_directivity_filter.storeInFDomain(self.filter_fftw_plan)


        self.filter_list_path = filter_list_name
        self.filter_list = open(self.filter_list_path, 'r')

        self.headphone_filter = None

        # format: [key,{filter}]
        self.filter_dict = {}
        self.late_reverb_filter_dict = {}
        self.directivity_dict = {}

        # Lists and KDTrees for filter searches
        self.filter_arr = list(list())
        self.late_reverb_arr = list(list())
        self.directivity_arr = list(list())
        self.filter_tree = 0
        self.late_reverb_tree = 0
        self.directivity_tree = 0

        # Start to load filters
        self.load_filters()

    def parse_filter_list(self):
        """
        Generator for filter list lines

        Lines are assumed to have a format like
        0 0 40 1 1 0 brirWav_APA/Ref_A01_1_040.wav

        The headphone filter starts with HPFILTER instead of the positions.

        Lines can be commented with a '#' as first character.

        :return: Iterator of (Pose, filter-path) tuples
        """

        for line in self.filter_list:

            # comment out lines in the list with a '#'
            if line.startswith('#') or line == "\n":
                continue

            line_content = line.split()
            filter_path = line_content[-1]

            #if line.startswith('HPFILTER'):
            # handle headphone filter
            if line.startswith('HPFILTER'):
                if self.useHeadphoneFilter:
                    self.log.info("Loading headphone filter: {}".format(filter_path))
                    self.headphone_filter = Filter(self.load_filter(filter_path, FilterType.Filter), self.headphone_ir_blocks, self.block_size)
                    self.headphone_filter.storeInFDomain(self.hp_filter_fftw_plan)
                    continue
                else:
                    #self.headphone_filter = Filter(self.load_filter(filter_path), self.ir_blocks, self.block_size)
                    self.log.info("Skipping headphone filter: {}".format(filter_path))
                    continue
                

            #filter_value_list = tuple(line_content[0:-1])
            #pose = Pose.from_filterValueList(filter_value_list)
            
            # handle normal filters and late reverb filters
            #filter_value_list = tuple(line_content[1:-1])
            #filter_pose = Pose.from_filterValueList(filter_value_list)
            filter_type = FilterType.Undefined
            
            if line_content[0].isdigit():
                filter_type = FilterType.Filter
                filter_value_list = tuple(line_content[:-1])
                filter_pose = Pose.from_filterValueList(filter_value_list)
            elif line.startswith('FILTER'):
                filter_type = FilterType.Filter
                filter_value_list = tuple(line_content[1:-1])
                filter_pose = Pose.from_filterValueList(filter_value_list)
            elif line.startswith('LATEREVERB'):
                if self.useSplittedFilters:
                    self.log.info("Loading late reverb filter: {}".format(filter_path))
                    filter_type = FilterType.LateReverbFilter
                    filter_value_list = tuple(line_content[1:-1])
                    filter_pose = Pose.from_filterValueList(filter_value_list)
                else:
                    self.log.info("Skipping LATEREVERB filter: {}".format(filter_path))
                    continue
            elif line.startswith('DIRECTIVITY'):
                filter_type = FilterType.Directivity
                filter_value_list = tuple(line_content[1:-1])
                filter_pose = Pose.from_filterValueList(filter_value_list)
            else:
                filter_type = FilterType.Undefined
                raise RuntimeError("Filter identifier wrong or missing")


            #yield pose, filter_path
            yield filter_pose, filter_path, filter_type

    def load_filters(self):
        """
        Load filters from files

        :return: None
        """

        self.log.info("Start loading filters...")
        start = time.time()
        parsed_filter_list = list(self.parse_filter_list())

        for filter_pose, filter_path, filter_type in parsed_filter_list:
        #for i, (pose, filter_path, filter_type) in enumerate(self.parse_filter_list()):
            # Skip undefined types (e.g. old format)
            if filter_type == FilterType.Undefined:
                continue
            
            # check for missing filters and throw exception if not found
            #if not Path(filter_path).exists():
            #    self.log.warn(f'Wavefile not found: {fn_filter}')
            #    raise FileNotFoundError(f'File {fn_filter} is missing.')
            
            self.log.debug(f'Loading {filter_path}')
            if filter_type == FilterType.Filter:
                # preprocess filters and put them in a dict
                current_filter = Filter(self.load_filter(filter_path, filter_type), self.ir_blocks, self.block_size)
                
                # apply fade out to all filters
                #current_filter.apply_fadeout(self.crossFadeOut)
                current_filter.storeInFDomain(self.filter_fftw_plan)
                
                # create key and store in dict
                key = filter_pose.create_key()
                self.filter_dict.update({key: current_filter})

                # add pose values to filter array
                self.filter_arr.append(list(map(float, filter_pose.orientation[0:2])))
            
            if filter_type == FilterType.LateReverbFilter:
                # preprocess late reverb filters and put them in a separate dict
                current_filter = Filter(self.load_filter(filter_path, filter_type), self.late_ir_blocks, self.block_size)
                
                # apply fade in to all late reverb filters
                #current_filter.apply_fadein(self.crossFadeIn)
                current_filter.storeInFDomain(self.late_filter_fftw_plan)
                
                #create key and store in dict
                key = filter_pose.create_key()
                self.late_reverb_filter_dict.update({key: current_filter})

                # add pose values to filter array
                self.late_reverb_arr.append(list(map(float, filter_pose.orientation[0:2])))

            if filter_type == FilterType.Directivity:
                # preprocess late reverb filters and put them in a separate dict
                current_filter = Filter(self.load_filter(filter_path, filter_type), self.dir_ir_blocks, self.block_size)

                current_filter.storeInFDomain(self.filter_fftw_plan)

                # create key and store in dict
                key = filter_pose.create_key()
                self.directivity_dict.update({key: current_filter})

                # add pose values to filter array
                self.directivity_arr.append(list(map(float, filter_pose.orientation[0:2])))

        #print(self.filter_arr)
        #print(self.late_reverb_arr)
        #print(self.directivity_arr)

        # build KDTrees for filter list items to do nearest neighbour search
        # TODO: KDTree for late reverb probably not needed, but... meh... maybe in the future it will
        self.filter_tree = KDTree(self.filter_arr)
        self.late_reverb_tree = KDTree(self.late_reverb_arr)
        self.directivity_tree = KDTree(self.directivity_arr)

        end = time.time()
        self.log.info("Finished loading filters in" + str(end-start) + "sec.")
        #self.log.info("filter_dict size: {}MiB".format(total_size(self.filter_dict) // 1024 // 1024))

    def get_filter(self, pose):
        """
        Searches in the dict if key is available and return corresponding filter
        When no filter is found, defaultFilter is returned which results in silence

        :param pose
        :return: corresponding filter for pose
        """

        find_me = pose.orientation[0:2]
        d, i = self.filter_tree.query(find_me)
        fvl = self.filter_arr[i] + [0, 0, 0, 0, 0, 0, 0]
        newpose = Pose.from_filterValueList(fvl)

        key = newpose.create_key()

        if key in self.filter_dict:
            self.log.info("Filter found: key: {}".format(key))
            result_filter = self.filter_dict.get(key)
            if result_filter.filename is not None:
                self.log.info("   use file:: {}".format(result_filter.filename))
            return result_filter
        else:
            self.log.warning('Filter not found: key: {}'.format(key))
            return self.default_filter

    def get_late_reverb_filter(self, pose):
        find_me = pose.orientation[0:2]
        d, i = self.late_reverb_tree.query(find_me)
        fvl = list(map(int, self.late_reverb_arr[i] + [0, 0, 0, 0, 0, 0, 0]))
        newpose = Pose.from_filterValueList(fvl)

        key = newpose.create_key()
        
        if key in self.late_reverb_filter_dict:
            self.log.info(f'Late Reverb Filter found: key: {key}')
            return self.late_reverb_filter_dict.get(key)
        else:
            self.log.warning(f'Late Reverb Filter not found: key: {key}')
            return self.default_late_reverb_filter

    def get_directivity_filter(self, pose):
        find_me = pose.orientation[0:2]
        d, i = self.directivity_tree.query(find_me)
        fvl = self.directivity_arr[i] + [0, 0, 0, 0, 0, 0, 0]
        newpose = Pose.from_filterValueList(fvl)

        key = newpose.create_key()

        if key in self.directivity_dict:
            self.log.info(f'Directivity Filter found: key: {key}')
            return self.directivity_dict.get(key)
        else:
            self.log.warning(f'Directivity Filter not found: key: {key}')
            return self.default_directivity_filter

    def close(self):
        self.log.info('FilterStorage: close()')
        # TODO: do something in here?

    def get_headphone_filter(self):
        if self.headphone_filter is None:
            raise RuntimeError("Headphone filter not loaded")

        return self.headphone_filter

    def load_filter(self, filter_path, filter_type):
        if filter_type != FilterType.Directivity:
            current_filter, fs = sf.read(filter_path, dtype='float32')
        else:
            current_filter, fs = sf.read(filter_path, dtype='float32', always_2d=True)
            # for some reason always_2d does nothing...
            current_filter = np.column_stack((current_filter, current_filter))

        filter_size = np.shape(current_filter)

        ## Question: is this still needed?
        
        #if not self.useSplittedFilters:
        if filter_type == FilterType.Filter:
            # Fill filter with zeros if to short
            if filter_size[0] < self.ir_size:
                self.log.warning('Filter too short: Fill up with zeros')
                current_filter = np.concatenate((current_filter, np.zeros(
                    (self.ir_size - filter_size[0], 2), np.float32)), 0)

        if filter_type == FilterType.Filter:
            if filter_size[0] > self.ir_size:
                self.log.warning('Filter too long: shorten')
                current_filter = current_filter[:self.ir_size]
        elif filter_type == FilterType.LateReverbFilter:
            if filter_size[0] > self.lateReverbSize:
                self.log.warning('Reverb Filter too long: shorten')
                current_filter = current_filter[:self.lateReverbSize]
        elif filter_type == FilterType.Directivity:
            if filter_size[0] > self.directivitySize:
                self.log.warning('Directivity filter too long: shorten')
                current_filter = current_filter[:self.directivitySize]
            elif filter_size[0] < self.directivitySize:
                self.log.warning('Directivity filter too short: Fill up with zeroes')
                current_filter = np.concatenate((current_filter, np.zeros(
                    (self.directivitySize - filter_size[0], 2), np.float32)), 0)
        # TODO: Check if shorten filter is needed for directivity filters

        return current_filter

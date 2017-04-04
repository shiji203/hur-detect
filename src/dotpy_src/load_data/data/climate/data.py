


import sys
import os
import numpy as np
import threading
import netCDF4 as nc
from netCDF4 import MFDataset
from os import listdir, system
from os.path import isfile, join, isdir
import imp
import itertools
import time
import inspect
import copy
from util import get_camfiles,convert_nc_data_to_tensor
# from labels.yolo_maker import make_yolo_masks_for_dataset, make_multiple_yolo_masks
import random
from configs import configs as climate_configs
import collections
from dotpy_src.load_data.configs import configs



configs.update(climate_configs)



def correct_class_labels(lbls, tf_mode=True):
    """subtract class labels by 1, so labels used to be 1-4 now 0-3 and 0 is still 0"""
    if tf_mode:
        cl_index = 5
        lbls[:,:,:,cl_index] = lbls[:,:,:,cl_index] - 1
        lbls[:,:,:,cl_index] = np.where(lbls[:,:,:,cl_index]==-1,
                                        np.zeros_like(lbls[:,:,:,cl_index]),
                                        lbls[:,:,:,cl_index] )
    else:
        assert False, "not implemented"
    return lbls
    



class ClimateNC(object):
    def __init__(self,filepaths,shape,crop_indices=None, crop_stride=None, variables=["TMQ", "VBOT", "PSL"],
                 time_step_sample_freq=1, time_steps_per_example=1,time_steps_per_file=8, is_label=False):
        
        assert time_steps_per_example == 1, "3d not quite supported for labels"
        frame = inspect.currentframe()
        # set self.k = v for every k,v pair in __init__ except self of course
        self.set_constructor_args(frame)
        self.is_label = is_label
        self.num_files = len(self.filepaths)
        self.examples_per_file = (time_steps_per_file / time_step_sample_freq) / time_steps_per_example
        self.total_examples = self.num_files * self.examples_per_file


    def set_constructor_args(self,frame):
        #set data members for object from constructor args
        _, _, _, params = inspect.getargvalues(frame)
        del params["frame"]
        for k,v in params.iteritems():
            setattr(self,k,v)
        
        
    
    def shuffle(self, seed):
        '''only shufflez files'''
        rng = np.random.RandomState(seed)
        random.shuffle(self.filepaths, random=rng.uniform)
        if self.crop_indices:
            rng = np.random.RandomState(seed)
            random.shuffle(self.crop_indices, random=rng.uniform)
            
        
        

    
    #overloading of bracket operators
    def __getitem__(self, slice_):
        slices = self.convert_slice_to_file_and_ex_inds(slice_)
        if slices is None:
            z_shape = tuple([0] + list(self.shape))
            data = np.zeros(z_shape)
        else:
            file_slice = slices["file_slice"]
            ex_slice = slices["ex_slice"]
            filepaths = self.filepaths[file_slice]
            if self.crop_indices:
                crop_indices = self.crop_indices[file_slice]
            else:
                crop_indices = None

            tens = self.grab_data_chunk(filepaths, crop_indices)

            data = tens[ex_slice]

            if self.is_label:
                data = correct_class_labels(data, tf_mode=True)
        
        return data
    
    
    def convert_slice_to_file_and_ex_inds(self, slice_):
        if isinstance(slice_, slice):
            start, stop, step = [getattr(slice_,k) for k in ["start", "stop", "step"]]
            assert step==1 or step is None, "step must be 1 or None"
        
        elif isinstance(slice_, int):
            start, stop = [slice_, slice_ + 1]
            
        slices =  self.get_file_and_ex_inds(start, stop)
        return slices
        
        
        
            
    def get_file_and_ex_inds(self, start, stop):
        if start == stop:
            return None
            
        #file start stop indices to index filenames
        file_start, file_stop = self.get_file_ind(start), self.get_file_ind(stop)
        
        # get some useful numbers
        tot_examples_desired = stop - start 
        
        #relative example indices after examples read in
        ex_start = self.get_relative_ex_ind(start)
        ex_stop = ex_start + tot_examples_desired

        file_slice = slice(file_start,file_stop)
        ex_slice = slice(ex_start,ex_stop)
        
        return {"file_slice":file_slice, "ex_slice": ex_slice}
    

    

    def get_file_ind(self,ex_ind):
        return ex_ind / self.examples_per_file

    def get_relative_ex_ind(self, ex_ind):
        return ex_ind % self.examples_per_file
        
        
        
    def grab_data_chunk(self, filepaths, crop_indices=None):
        """grabs input data (converts filepaths to np tensors)
        returns len(filepaths)*4, 16, 768,1152 array"""
        


        dsets=[]
        for filepath in filepaths:
            dataset=nc.Dataset(filepath)
            if "x_coord" in self.variables:
                is_label = True
            else:
                is_label = False
            tensor = convert_nc_data_to_tensor(dataset,
                                               self.variables, is_label,
                                               self.time_step_sample_freq,
                                               self.time_steps_per_example)
            xdim = tensor.shape[2]
            #hard code crop to 768
            tensor = tensor[:,:,:,:xdim]
            dsets.append(tensor)
        tensor = np.vstack(tuple(dsets))
            
        
 
        return tensor



def get_data(type_, num_examples):

    data_list_dir = configs["data_list_dir"]
    im_files = get_files_from_list(data_list_dir, type_, "image")
    lbl_files = get_files_from_list(data_list_dir, type_, "label")
    if num_examples != -1:
        num_files = (configs["time_step_sample_frequency"] * num_examples) / configs["time_steps_per_file"]
        if num_files <= len(im_files):
            im_files = im_files[:num_files]
            lbl_files = lbl_files[:num_files]

    crop_indices=None


    images = ClimateNC(filepaths=im_files, shape=configs["input_shape"],
                                 crop_indices=crop_indices, 
                                 time_step_sample_freq=configs["time_step_sample_frequency"],
                                 variables=configs["image_variables"],
                                 time_steps_per_example=configs["time_steps_per_example"])


    labels = ClimateNC(filepaths=lbl_files,shape=configs["label_shape"],
                                 crop_indices=crop_indices,
                                 variables=configs["label_variables"],
                                 time_step_sample_freq=configs["time_step_sample_frequency"],
                                 time_steps_per_example=configs["time_steps_per_example"])


    return images,labels



def get_files_from_list(list_dir, type_, images_or_labels_or_crop_indices):
    with open(join(list_dir, type_ + "_" + images_or_labels_or_crop_indices + "_files.txt"), "r") as f:
        return [line.strip("\n") for line in f.readlines()]






import matplotlib; matplotlib.use("agg")


import pickle
import lasagne
from lasagne.layers import *
from lasagne.nonlinearities import *
from lasagne.objectives import *
from lasagne.init import HeNormal
import theano
from theano import tensor as T
import sys
import numpy as np
from copy import deepcopy
#enable importing of notebooks
import inspect
import os

if "THEANO_FLAGS" in os.environ and "gpu" in os.environ["THEANO_FLAGS"]:
        from lasagne.layers import dnn
from helper_fxns import get_detec_loss, softmax3D, softmax4D, AccuracyGetter
import copy
#if __name__ == "__main__":
    #from data_loader import load_classification_dataset, load_detection_dataset



def get_hyperparams(frame):
    args, _, _, values = inspect.getargvalues(frame)
    #return dict(zip(args,values))
    #del values['frame']
    return values

def build_network(kwargs):
    
    '''Takes a pretrained classification net and adds a few convolutional layers on top of it
    and defines a detection loss function'''
    '''Args:
                      
                      num_convpool: number of conv layer-pool layer pairs
                      delta: smoothing constant to loss function (ie sqrt(x + delta)) 
                            -> if x is 0 gradient is undefined
                      num_filters
                      num_fc_units
                      num_extra_conv: conv layers to add on to each conv layer before max pooling
                      nonlinearity: which nonlinearity to use throughout
                      n_boxes: how many boxes should be predicted at each grid point,
                      nclass: how many classes are we predicting,
                      grid_size: size of the grid that encodes various 
                                locations of image (ie in the YOLO paper they use 7x7 grid)
                      w_init: weight intitialization
                      dropout_p: prob of dropping unit
                      coord_penalty : penalty in YOLO loss function for getting coordinates wrong
                      nonobj_penalty: penalty in YOLO loss for guessing object when there isn't one
                      learning_rate
                      weight_decay
                      momentum
                      load: whether to load weights or not
                      load_path: path for loading weights'''

    if kwargs["im_dim"] == 3:
        input_var = T.tensor5('input_var') 
    else:
        input_var = T.tensor4('input_var')
    target_var = T.tensor4('target_var') #is of shape (grid_size, grid_size,(n_boxes* 5 + nclass)
    
    print "Building model and compiling functions..." 
    
    #make layers
    networks = build_layers(input_var,kwargs)
    
    #load in any pretrained weights
    if kwargs['ae_load_path'] != "None":
        networks['ae'] = load_weights(kwargs['ae_load_path'], networks['ae'])
        
    if kwargs['yolo_load_path'] != "None":
        networks['yolo'] = load_weights(kwargs['yolo_load_path'], networks['yolo'])
    
    #compile theano functions
    fns = make_fns(networks, input_var, target_var, kwargs)
     

    return fns, networks


def build_layers(input_var, nk):
    '''nk: network_kwargs'''
    '''conv, extra_convs, pool multiple times then fc with dropout, fc with dropout and softmax then reshape'''
    
    '''total number of conv layers is num_convpool * (1 + num_extra_conv)'''
    
    filter_dim = nk['filter_dim']
    num_layers = nk['num_layers']
    

    filters_list = [128, 256, 512, 768, 1024, 1280]
    conv = lasagne.layers.InputLayer(shape=nk['input_shape'])
    for i in range(num_layers):

        
        num_filters = int(nk["filters_scale"] * filters_list[i])

        
        if nk["im_dim"] == 3:
            conv = dnn.Conv3DDNNLayer(conv, num_filters=num_filters, filter_size=(3,5,5),pad=(1,2,2), stride=(1,2,2)) 
        else:
            #print nk["filters_scale"]
            #print num_filters
            conv = Conv2DLayer(conv, 
                                  num_filters=num_filters, 
                                  filter_size=nk['filter_dim'], 
                                  pad=nk['filter_dim'] / 2, 
                                  stride=2,
                                  W=HeNormal(),
                                  nonlinearity=LeakyRectify(0.1))

        

    encoder = conv
    hid_fmap = conv
    
 
    if nk["yolo_batch_norm"]:
        encoder = BatchNormLayer(encoder)
    
    if nk["im_dim"] == 3:
            #encoder = FeaturePoolLayer(encoder, pool_size=640, axis=1)
            box_conf = dnn.Conv3DDNNLayer(encoder, num_filters=2, filter_size=(3,3,3), 
                                          stride=(2,1,1), pad=(1,1,1), nonlinearity=softmax4D)
    
            #box_conf = NonlinearityLayer(box_conf, softmax4D)
            class_conf = dnn.Conv3DDNNLayer(encoder, num_filters=nk['num_classes'], filter_size=(3,3,3), stride=(2,1,1), pad=(1,1,1), nonlinearity=softmax4D)
            #class_conf = NonlinearityLayer(class_conf, softmax4D)
            coord_net = dnn.Conv3DDNNLayer(encoder, num_filters=4, filter_size=(3,3,3), stride=(2,1,1), pad=(1,1,1), W=nk['w_init'],
                            nonlinearity=rectify)
            #outputs a batch_size x 10 x 4 x 12 x 18 
            bbox_reg = ConcatLayer([coord_net,box_conf, class_conf])
            #print get_output_shape(bbox_reg,input_shapes=(1,16,8,768,1152))
            s = get_output_shape(bbox_reg)
            # reshape to be like the 2D case -> batch_size*time_steps(4) x 10 x 12 x 18
            net = ExpressionLayer(bbox_reg, function=lambda g: g.transpose((0,2,1,3,4)),
                                  output_shape=(nk["batch_size"],s[2], s[1],s[3], s[4]))
            #print get_output_shape(net,input_shapes=(1,16,8,768,1152))
            # after transpose
            s = get_output_shape(net)
            bbox_reg = ReshapeLayer(net, shape=(nk["batch_size"],s[1], s[2],s[3], s[4]))
            #print get_output_shape(bbox_reg,input_shapes=(1,16,8,768,1152))
            
            
    
    else:   
        box_conf = Conv2DLayer(encoder, num_filters=2, filter_size=3, pad=1, nonlinearity=linear)
        box_conf = NonlinearityLayer(box_conf, softmax3D)
        class_conf = Conv2DLayer(encoder, num_filters=nk['num_classes'], filter_size=3, pad=1, nonlinearity=linear)
        class_conf = NonlinearityLayer(class_conf, softmax3D)
        coord_net = Conv2DLayer(encoder, num_filters=4, filter_size=3,pad=1, W=nk['w_init'],
                                nonlinearity=rectify)
    
    
        bbox_reg = ConcatLayer([coord_net,box_conf, class_conf])
    
    for layer in get_all_layers(conv)[::-1]:
        
        if nk["batch_norm"]:
            conv = batch_norm(conv)
        if isinstance(layer, InputLayer):
            break
        
        conv = InverseLayer(conv, layer)
        

    
    
    return {'yolo': bbox_reg, 'ae':conv, 'hid_fmap':hid_fmap}#, "decoder":decoder_layers}
        

def load_weights(pickle_file_path, network):
    '''grabs weights from an npz file'''
    old_params = pickle.load(open(pickle_file_path, 'r'))

    set_all_param_values(network, old_params)
    return network
    

def make_fns(networks,input_var, target_var, kwargs ):
    '''Compiles theano train, test, box_fns'''
    #deterministic determines whether to use dropout or not in forward pass
    #transpose output to match what loss expects
    for k,v in networks.iteritems():
        kwargs['logger'].info("\n" + k + ": \n")
        for lay in get_all_layers(v):
            kwargs["logger"].info(str(lay) + ", " + str(get_output_shape(lay)))
    
    yolo = networks['yolo']
    ae = networks['ae']
    hid_fmap = networks['hid_fmap']
    yolo_test_prediction = lasagne.layers.get_output(yolo, deterministic=True, inputs=input_var)
    yolo_prediction = lasagne.layers.get_output(yolo, deterministic=False,inputs=input_var)
    
    ae_test_prediction = lasagne.layers.get_output(ae, deterministic=True,inputs=input_var)
    ae_prediction = lasagne.layers.get_output(ae, deterministic=False,inputs=input_var)
    hid_fmap_pred = get_output(hid_fmap, deterministic=True, inputs=input_var)
    
    def make_hid_fn():
        hid_fn = theano.function([input_var], hid_fmap_pred)
        return hid_fn
    
    def make_loss(yolo_pred, ae_pred):
        yolo_loss, terms = make_yolo_loss(yolo_pred)
        ae_loss = make_ae_loss(ae_pred)
        
        #just to make sure we don't compute this if we don't want to
        if kwargs['lambda_ae'] == 0.:
            loss = yolo_loss
        else:
            loss = yolo_loss + kwargs['lambda_ae'] * ae_loss
        return loss, yolo_loss, ae_loss, terms
    
    def make_yolo_loss(pred):
        yolo_loss, terms = get_detec_loss(pred, target_var, kwargs)
        
        weightsl2 = lasagne.regularization.regularize_network_params(yolo, lasagne.regularization.l2)
        weight_decay_term = kwargs['weight_decay'] * weightsl2
        loss = yolo_loss + weight_decay_term
        return loss, terms
    
    def make_ae_loss(pred):
        loss = squared_error(pred, input_var)
        weightsl2 = lasagne.regularization.regularize_network_params(ae, lasagne.regularization.l2)
        loss += kwargs['weight_decay'] * weightsl2
        return loss.mean()
        
    
    

    def make_train_fn(test=False):
        '''takes as input the input, target vars and ouputs a loss'''
        if test:
            loss, yolo_loss, ae_loss, terms =  make_loss(yolo_test_prediction, ae_test_prediction)
        else:   
            loss, yolo_loss, ae_loss, terms =  make_loss(yolo_prediction, ae_prediction)
        
        #coord_term, size_term, conf_term, no_obj_conf_term, xentropy_term = terms
        terms = T.stack(terms)
        #only using params from yolo here -> because decoder has no new params -> tied weights
        if not test:
            params = lasagne.layers.get_all_params(yolo, trainable=True) #+ lasagne.layers.get_all_params(ae, trainable=True)
            updates = lasagne.updates.adam(loss, params,learning_rate=kwargs['learning_rate'])

            fn = theano.function([input_var, target_var], [loss,ae_loss,
                                                                yolo_loss, terms ], 
                                                                updates=updates)
        else:
            fn = theano.function([input_var, target_var], [loss,ae_loss,
                                                    yolo_loss, terms ])
        
        def fxn(inp, target):
            loss, ae_loss, yolo_loss, terms = fn(inp, target)
            coord_term, size_term, conf_term, no_obj_conf_term, xentropy_term = terms
            
            return dict(loss=loss, ae_loss=ae_loss, 
                        yolo_loss=yolo_loss) 
                        
                        
                        #coord_term=coord_term, size_term=size_term, conf_term=conf_term, 
                        #no_obj_conf_term=no_obj_conf_term, xentropy_term=xentropy_term)
            
            
            
            
        return fxn
        
    
    def make_test_or_val_fn():
        
        return make_train_fn(test=True)
    
    

    
    def make_yolo_pred_fn():
        '''takes as input the input, target vars and outputs the predicted grid'''
        pred_fn = theano.function([input_var], yolo_test_prediction)
        return pred_fn
    
    def make_ae_pred_fn():
        pred_fn = theano.function([input_var], ae_test_prediction)
        return pred_fn
        
    def make_box_fn():
        pred_fn = make_yolo_pred_fn()
        def box_fn(x, y, num_classes=kwargs['num_classes']):
            y_tensor = y
            pred_tensor = pred_fn(x)
            acc_obj = AccuracyGetter(kwargs)
            pred_boxes, gt_boxes = acc_obj.get_all_boxes(pred_tensor=pred_tensor, 
                                                 y_tensor=y_tensor)
            return pred_boxes, gt_boxes
        return box_fn
    
   
        
            
    def make_acc_fn():
        '''takes as input the input, target vars and outputs the predicted and the ground truth boxes)'''
        pred_fn = make_yolo_pred_fn()
        def acc_fn(inp, gt, conf_thresh=None, iou_thresh=None):
            pred = pred_fn(inp)
            if conf_thresh is not None:
                kwargs["conf_thresh"] = conf_thresh
            if iou_thresh is not None:
                kwargs["iou_thresh"] = iou_thresh
            acc_getter = AccuracyGetter(kwargs)
            acc_dict = acc_getter.get_scores(pred,gt)
            return acc_dict
    
        return acc_fn
    
    train_fn = make_train_fn()
    test_or_val_fn = make_test_or_val_fn()
    acc_fn = make_acc_fn()
    yolo_pred_fn = make_yolo_pred_fn()
    ae_pred_fn = make_ae_pred_fn()
    box_fn = make_box_fn()
    hid_fn = make_hid_fn()
    return {"tr":train_fn, "val":test_or_val_fn, "test":test_or_val_fn, "acc": acc_fn, "rec": ae_pred_fn, "box": box_fn, "hid": hid_fn}



# import theano

# a = theano.tensor.scalar()
# b = theano.tensor.scalar()
# c = theano.tensor.scalar()
# x = theano.tensor.stack([a, b, c])

# x.eval({a:2,b:3,c:4})






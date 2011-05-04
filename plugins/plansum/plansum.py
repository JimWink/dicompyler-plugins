try:
    import dicomgui, guiutil
except ImportError:
    #Running as __main__, not as a plugin
    pass

import wx
from wx.lib.pubsub import Publisher as pub
import numpy as np
import dicom
import matplotlib.mlab as mlab
import numpy.testing as npt
    
import unittest
import os
import threading, Queue


def pluginProperties():
    """Properties of the plugin."""

    props = {}
    props['name'] = 'Sum RT Dose'
    props['description'] = "Adds multiple RT Dose objects together."
    props['author'] = 'Stephen Terry'
    props['version'] = 0.2
    props['plugin_type'] = 'menu'
    props['plugin_version'] = 1
    props['min_dicom'] = ['rtdose','rtplan']
    props['recommended_dicom'] = ['rtdose','rtplan']
    props['documentation'] = \
        'http://code.google.com/p/dicompyler-plugins/wiki/plansum'

    return props

class plugin:
    
    def __init__(self, parent):
        
        self.parent = parent
        
        # Set up pubsub
        pub.subscribe(self.OnUpdatePatient, 'patient.updated.raw_data')
        pub.subscribe(self.OnUpdatePatient, 'patient.updated.parsed_data')
        
    def OnUpdatePatient(self, msg):
        """Update and load the patient data."""
        if msg.data.has_key('rtdose'):
            self.rtdose = msg.data['rtdose']
        if msg.data.has_key('rxdose'):
            self.rxdose = msg.data['rxdose']
        if msg.data.has_key('structures'):
            self.structures = msg.data['structures']
        if msg.data.has_key('rtplan'):
            self.rtplan = msg.data['rtplan']
    
    def OnDestroy(self, evt):
        """Unbind to all events before the plugin is destroyed."""

        pub.unsubscribe(self.OnUpdatePatient)
        
    def pluginMenu(self, evt):
        """Open a new RT Dose object and sum it with the current dose"""
        self.ptdata = dicomgui.ImportDicom(self.parent)
       
        if (self.ptdata['rtplan'].SeriesInstanceUID != \
            self.rtplan.SeriesInstanceUID):
            dlg = wx.MessageDialog(self.parent, 
                               "The image sets for both doses do not match.",
                               "Cannot sum doses.", wx.OK | wx.ICON_WARNING)
            dlg.ShowModal()
            dlg.Destroy()
            return
        
        old = self.rtdose
        new = self.ptdata['rtdose']
        dlgProgress = guiutil.get_progress_dialog(
                wx.GetApp().GetTopWindow(),
                "Creating Plan Sum...")
        q = Queue.Queue()
        threading.Thread(target=SumPlan, args=(old, new, q,
                                        dlgProgress.OnUpdateProgress)).start()
        dlgProgress.ShowModal()
        dlgProgress.Destroy()
        sumDicomObj = q.get()
        self.ptdata['rtdose'] = sumDicomObj
        if self.ptdata.has_key('rxdose') and self.rxdose:
            self.ptdata['rxdose'] = self.rxdose + self.ptdata['rxdose']
        pub.sendMessage('patient.updated.raw_data', self.ptdata)
        
        
def SumPlan(old, new, q, progressFunc=None, interp_method=''):
    """ Given two Dicom RTDose objects, returns a summed RTDose object"""
    """The summed RTDose object will consist of pixels inside the region of 
    overlap between the two pixel_arrays.  The pixel spacing will be the 
    coarser of the two objects in each direction.  The new DoseGridScaling
    tag will be the sum of the tags of the two objects.
    
    interp_method: A string that is one of ['scipy','weave','python'].  
        This forces SumPlan to use a particular interpolation method, even if 
        the dose objects could be directly summed.  Used for unit testing."""
    
    #Recycle the new Dicom object to store the summed dose values
    sum_dcm = new
    
    #Test if dose grids are coincident.  If so, we can directly sum the 
    #pixel arrays.
    if (old.ImagePositionPatient == new.ImagePositionPatient and
        old.pixel_array.shape == new.pixel_array.shape and
        old.PixelSpacing == new.PixelSpacing and
        old.GridFrameOffsetVector == new.GridFrameOffsetVector and 
        not interp_method):
        print "PlanSum: Using direct summation"
        wx.CallAfter(progressFunc, 0, 1, 'Using direct summation')
        sum = old.pixel_array*old.DoseGridScaling + \
                new.pixel_array*new.DoseGridScaling
        
    else:    
        if not interp_method:
            interp_method = 'scipy'
        #Compute mapping from xyz (physical) space to ijk (index) space
        scale_old = np.array([old.PixelSpacing[0],old.PixelSpacing[1],
                    old.GridFrameOffsetVector[1]-old.GridFrameOffsetVector[0]])
        
        scale_new = np.array([new.PixelSpacing[0],new.PixelSpacing[1],
                    new.GridFrameOffsetVector[1]-new.GridFrameOffsetVector[0]])
        
        scale_sum = np.maximum(scale_old,scale_new)
        
        #Find region of overlap
        xmin = np.array([old.ImagePositionPatient[0],
                         new.ImagePositionPatient[0]])
        ymin = np.array([old.ImagePositionPatient[1],
                         new.ImagePositionPatient[1]])
        zmin = np.array([old.ImagePositionPatient[2],
                         new.ImagePositionPatient[2]])
        xmax = np.array([old.ImagePositionPatient[0] + 
                         old.PixelSpacing[0]*old.Columns,
                         new.ImagePositionPatient[0] + 
                         new.PixelSpacing[0]*new.Columns])
        ymax = np.array([old.ImagePositionPatient[1] + 
                         old.PixelSpacing[1]*old.Rows,
                         new.ImagePositionPatient[1] +
                          new.PixelSpacing[1]*new.Rows])
        zmax = np.array([old.ImagePositionPatient[2] + 
                         scale_old[2]*len(old.GridFrameOffsetVector),
                         new.ImagePositionPatient[2] + 
                         scale_new[2]*len(new.GridFrameOffsetVector)])
        x0 = xmin[np.argmin(abs(xmin))]
        x1 = xmax[np.argmin(abs(xmax))]
        y0 = ymin[np.argmin(abs(ymin))]
        y1 = ymax[np.argmin(abs(ymax))]
        z0 = zmin[np.argmin(abs(zmin))]
        z1 = zmax[np.argmin(abs(zmax))]
        
        
        sum_ip = np.array([x0,y0,z0])
        
        #Create index grid for the sum array
        i,j,k = np.mgrid[0:int((x1-x0)/scale_sum[0]),
                         0:int((y1-y0)/scale_sum[1]),
                         0:int((z1-z0)/scale_sum[2])] 
        
        x_vals = np.arange(x0,x1,scale_sum[0])
        y_vals = np.arange(y0,y1,scale_sum[1])
        z_vals = np.arange(z0,z1,scale_sum[2])
        
        #Create a 3 x i x j x k array of xyz coordinates for the interpolation.
        sum_xyz_coords = np.array([i*scale_sum[0] + sum_ip[0],
                                      j*scale_sum[1] + sum_ip[1],
                                      k*scale_sum[2] + sum_ip[2]])
        
        #Dicom pixel_array objects seem to have the z axis in the first index
        #(zyx).  The x and z axes are swapped before interpolation to coincide
        #with the xyz ordering of ImagePositionPatient
        sum = interpolate_image(np.swapaxes(old.pixel_array,0,2), scale_old, 
            old.ImagePositionPatient, sum_xyz_coords, interp_method,
            progressFunc)*old.DoseGridScaling + \
            interpolate_image(np.swapaxes(new.pixel_array,0,2), scale_new, 
            new.ImagePositionPatient, sum_xyz_coords, interp_method,
            progressFunc)*new.DoseGridScaling
        
        #Swap the x and z axes back
        sum = np.swapaxes(sum, 0, 2)
        sum_dcm.ImagePositionPatient = list(sum_ip)
        sum_dcm.Rows = len(y_vals)
        sum_dcm.Columns = len(x_vals)
        sum_dcm.NumberofFrames = len(z_vals)
        sum_dcm.PixelSpacing = [scale_sum[0],scale_sum[1]]
        sum_dcm.GridFrameOffsetVector = list(z_vals - sum_ip[2])
                

    sum_scaling = old.DoseGridScaling + new.DoseGridScaling
    
    sum = sum/sum_scaling
    sum = np.uint32(sum)
    
    sum_dcm.pixel_array = sum
    sum_dcm.BitsAllocated = 32
    sum_dcm.BitsStored = 32
    sum_dcm.HighBit = 31
    sum_dcm.PixelData = sum.tostring()
    sum_dcm.DoseGridScaling = sum_scaling
    wx.CallAfter(progressFunc, 1, 1, 'Done')
    q.put(sum_dcm)
  
def interpolate_image(input_array, scale, offset, xyz_coords, interp_method, 
                      progressFunc=None):
    """Interpolates an array at the xyz coordinates given"""
    """Parameters:
        input_array: a 3D numpy array
        
        scale: a list of 3 floats which give the pixel spacing in xyz
        
        offset: the xyz coordinates of the origin of the input_array
        
        xyz_coordinates: the coordinates at which the input is evaluated. 
        
        interp_method: A string that is one of ['scipy','weave','python'].  
            Forces the use of a particular interpolation method.  Used 
            for unit testing.  If the specified method is not available, the
            function will attempt the next method in order.
        
    The purpose of this function is to convert the xyz coordinates to
    index space, which scipy.ndimage.map_coordinates and trilinear_interp
    require.  Following the scipy convention, the xyz_coordinates array is
    an array of three  i x j x k element arrays.  The first element contains 
    the x axis value for each of the i x j x k elements, the second contains
    the y axis value, etc."""
        
    indices = np.empty(xyz_coords.shape)
    indices[0] = (xyz_coords[0] - offset[0])/scale[0]
    indices[1] = (xyz_coords[1] - offset[1])/scale[1]
    indices[2] = (xyz_coords[2] - offset[2])/scale[2]
    
    #scipy.ndimage.map_coordinates and trilinear.trilinear are much faster than
    #the python interpolation.  Use them if possible.
    if interp_method == 'scipy':

        try:
            import scipy.ndimage
            print "PlanSum: Using scipy.ndimage.map_coordinates"
            wx.CallAfter(progressFunc, 0, 1, 'Using SciPy')
            return scipy.ndimage.map_coordinates(input_array, indices, order=1)
        except ImportError:
            interp_method = 'weave'
    
    if interp_method == 'weave':
        
        try:
            import trilinear
            print "PlanSum: Using trilinear.trilinear"
            #Trilinear module requires strict typing of input to uint32
            input_array = np.uint32(input_array)
            output = np.zeros(indices[0].shape, dtype=np.float64)
            wx.CallAfter(progressFunc, 0, 1, 'Using trilinear weave')
            trilinear.trilinear(input_array, indices, output)           
            return output
        except ImportError:
            pass
    
    print "PlanSum: Using trilinear_interp"
    return trilinear_interp(input_array, indices, progressFunc)

def trilinear_interp(input_array, indices, progressFunc=None):
    output = np.empty(indices[0].shape)
    x_indices = indices[0]
    y_indices = indices[1]
    z_indices = indices[2]
    input_indices = np.indices(input_array.shape)
    intermediate = np.empty((x_indices.shape[0],
                             x_indices.shape[1],
                             input_array.shape[2]))
    
    for k in range(input_array.shape[2]):
        if progressFunc:
            wx.CallAfter(progressFunc, k, input_array.shape[2],
                'Interpolating x and y')
        intermediate[:,:,k] = mlab.griddata(input_indices[0,:,:,k].flatten(),
                                            input_indices[1,:,:,k].flatten(),
                                            input_array[:,:,k].flatten(),
                                            x_indices[:,:,0],y_indices[:,:,0])
        
    for k in range(output.shape[2]):
        if progressFunc:
            wx.CallAfter(progressFunc, k, output.shape[2],
                'Interpolating z')
        z0 = int(z_indices[0,0,k])
        z1 = z0 + 1
        if z1 == input_array.shape[2]:
            z1 = z0
        z = z_indices[0,0,k] % 1
        output[:,:,k] = (1-z)*intermediate[:,:,z0]+z*intermediate[:,:,z1]
        
    return output

class PlanSumTest(unittest.TestCase):
    
    def testPlanSum(self):
        ss = dicom.read_file('./testdata/rtss.dcm')
        rtd1 = dicom.read_file('./testdata/rtdose1.dcm')
        rtd2 = dicom.read_file('./testdata/rtdose2.dcm')

        sum = SumPlan(rtd1, rtd2)
        
        self.assertEqual(sum.pixel_array.shape,(4,10,8))
        self.assertEqual(sum.PixelSpacing, [5.,5.])
        
        for pos1,pos2 in zip(sum.ImagePositionPatient,[-15.44,-20.44,-7.5]):
            self.assertAlmostEqual(pos1,pos2,2)
            
        difference = abs(sum.pixel_array[1,4,3]*sum.DoseGridScaling - 3.006)
        delta = 0.01
        self.assertTrue(difference < delta,
                "difference: %s is not less than %s" % (difference, delta))
        
        difference = abs(sum.pixel_array[1,1,1]*sum.DoseGridScaling - 2.9)
        self.assertTrue(difference < delta,
                "difference: %s is not less than %s" % (difference, delta))
        
    def testInterpolation(self):
        
        rtd = dicom.read_file('../testdata/rtdose.dcm')
        a = SumPlan(rtd, rtd)
        b = SumPlan(rtd, rtd, interp_method='scipy')
        c = SumPlan(rtd, rtd, interp_method='weave')
        d = SumPlan(rtd, rtd, interp_method='python')
        
        npt.assert_equal(a.pixel_array,b.pixel_array)
        npt.assert_equal(a.pixel_array,c.pixel_array)
        npt.assert_equal(a.pixel_array,d.pixel_array)
        
                     
if __name__ == '__main__':
    unittest.main()
    
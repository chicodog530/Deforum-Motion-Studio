import unittest
import numpy as np
from geometry import sample_map,warp,GeometryPreview

class GeometryTests(unittest.TestCase):
    def test_identity(self):
        mx,my=sample_map(64,96,np.zeros(6),70,1)
        np.testing.assert_allclose(mx,np.broadcast_to(np.arange(64),(96,64)),atol=1e-5)
        np.testing.assert_allclose(my,np.broadcast_to(np.arange(96)[:,None],(96,64)),atol=1e-5)
    def test_z_scale(self):
        w,h=768,1120;z=5;fov=70
        mx,my=sample_map(w,h,[0,0,z,0,0,0],fov,1)
        sx=np.diff(mx[h//2]).mean();sy=np.diff(my[:,w//2]).mean()
        focal=1/np.tan(np.radians(fov)/2)
        expected=1-w/(w-1)*focal*(1/(1-z/200)-1)
        self.assertAlmostEqual(sx,expected,places=6)
        self.assertGreater(1/sx,1.03)
        self.assertNotAlmostEqual(1/sx,np.exp(z/400),places=2)
    def test_aspect_affects_horizontal_only(self):
        a,b=sample_map(64,96,[.3,0,0,0,0,0],70,1)
        c,d=sample_map(64,96,[.3,0,0,0,0,0],70,2)
        origin=np.broadcast_to(np.arange(64),(96,64))
        np.testing.assert_allclose(a-origin,2*(c-origin),atol=1e-5)
        np.testing.assert_array_equal(b,d)
    def test_fov_affects_scale(self):
        a,_=sample_map(64,96,[0,0,5,0,0,0],40,1)
        b,_=sample_map(64,96,[0,0,5,0,0,0],90,1)
        self.assertLess(np.diff(a[48]).mean(),np.diff(b[48]).mean())
    def test_independent_homogeneous_projection(self):
        # Independent 4x4 matrix implementation of the uploaded code's
        # row-vector world/view and FoVPerspectiveCameras projection.
        from geometry import rotation_xyz
        w,h=64,96;move=np.array([.2,-.1,1.7,.3,-.2,.15]);fov=70;aspect=1.3
        r=rotation_xyz(move[3:]);view=np.eye(4)
        view[:3,:3]=r;view[3,:3]=move[:3]*[-1,1,-1]/200
        focal=1/np.tan(np.radians(fov)/2);near,far=200,10000
        p=np.array([[focal/aspect,0,0,0],[0,focal,0,0],[0,0,far/(far-near),1],[0,0,-far*near/(far-near),0]])
        xx,yy=np.meshgrid(np.linspace(-1,1,w),np.linspace(-1,1,h))
        world=np.stack([xx,yy,np.ones_like(xx),np.ones_like(xx)],axis=-1)
        old=world@p;new=world@view@p
        offset=new[...,:2]/new[...,3:]-old[...,:2]/old[...,3:]
        mx,my=sample_map(w,h,move,fov,aspect)
        np.testing.assert_allclose(mx,np.arange(w)[None,:]-offset[...,0]*w/2,atol=2e-5)
        np.testing.assert_allclose(my,np.arange(h)[:,None]-offset[...,1]*h/2,atol=2e-5)
    def test_repeated_warp_and_seek(self):
        tracks=np.zeros((10,6));tracks[:,2]=1
        cfg={'W':64,'H':96,'fov_schedule':'0:(70)','aspect_ratio_schedule':'0:(1)'}
        preview=GeometryPreview(cfg,tracks)
        expected=preview.source.copy()
        for f in range(1,7):expected=warp(expected,tracks[f],70,1)
        np.testing.assert_array_equal(np.array(preview.seek(6, max_steps=100)[0]),expected)
        np.testing.assert_array_equal(np.array(preview.seek(0, max_steps=100)[0]),preview.source)
        np.testing.assert_array_equal(np.array(preview.seek(6, max_steps=100)[0]),expected)
    def test_recording_invalidation(self):
        tracks=np.zeros((10,6));cfg={'W':64,'H':96}
        p=GeometryPreview(cfg,tracks.copy());p.seek(5, max_steps=100)
        tracks[3,2]=5;p.update_tracks(tracks)
        expected=warp(p.source,tracks[3],70,1)
        np.testing.assert_array_equal(np.array(p.seek(5, max_steps=100)[0]),expected)
    def test_no_fake_depth_prediction(self):
        with self.assertRaisesRegex(ValueError,'depth warping OFF'):
            GeometryPreview({'use_depth_warping':True},np.zeros((10,6)))

if __name__=='__main__':unittest.main()

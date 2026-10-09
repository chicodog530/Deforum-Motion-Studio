import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
from motion import *
from controller import curve

class MotionTests(unittest.TestCase):
    def test_baseline_and_repair(self):
        path=Path(__file__).parent/'examples'/'working_baseline.txt'
        with self.assertRaisesRegex(ValueError,'comma'):load_config(path)
        cfg,tracks,repairs=load_config(path,True)
        self.assertEqual(tracks.shape,(800,6));self.assertEqual(repairs,['rotation_3d_z'])
        self.assertAlmostEqual(tracks[0,2],-1.75)
        self.assertAlmostEqual(tracks[200,3],-.5)
    def test_future_keyframes(self):
        x,_=parse_schedule('0:(0),900:(9)',800)
        self.assertAlmostEqual(x[-1],7.99)
    def test_expression_and_security(self):
        x,_=parse_schedule('0:(0.2*sin(2*pi*t/24))',48)
        self.assertAlmostEqual(x[6],.2)
        for source in ('0:(__import__("os").system("id"))','0:(t.__class__)','0:(10**99999)'):
            with self.assertRaises(ValueError):parse_schedule(source,10)
    def test_reject_bad_schedules(self):
        for source in ('0:(0),0:(1)','5:(1)','0:(1),','0:(1/0)'):
            with self.assertRaises(ValueError):parse_schedule(source,10)
    def test_preset_limits(self):
        for name in PRESETS:
            x=preset(name,800,24,strength=1)
            self.assertTrue(np.all(np.abs(x)<=LIMITS+1e-9))
            np.testing.assert_array_equal(x[[0,-1]],0)
    def test_pulse_return_plus_travel(self):
        times=np.arange(500)/100;env=np.zeros(500);env[100]=1;env[200]=.8
        pos=pulse_positions(env,times,120,24,strength=4)
        out,scale,clipped=combine(np.zeros((120,6)),.4,pos,[.3,.3,10,.3,.3,.3])
        self.assertEqual(scale,1);self.assertFalse(clipped)
        self.assertAlmostEqual(np.sum(out[:,2]-.4),0)
        self.assertGreater(np.max(out[:,2]),.4);self.assertLess(np.min(out[:,2]),.4)
    def test_headroom_preserves_return(self):
        pos=np.r_[np.linspace(0,4,5),np.linspace(4,0,5)]
        out,scale,_=combine(np.zeros((10,6)),.8,pos,[.3,.3,1,.3,.3,.3])
        self.assertLess(scale,1);self.assertGreater(scale,0)
        self.assertLessEqual(np.max(np.abs(out[:,2])),1+1e-9)
        self.assertAlmostEqual(np.sum(out[:,2]-.8),0)
    def test_bass_frequency_separation(self):
        sr=8000;t=np.arange(sr*3)/sr
        samples=np.sin(2*np.pi*70*t)*(t>1)*(t<1.2)+np.sin(2*np.pi*1200*t)*(t>2)*(t<2.2)
        env,times=bass_envelope(samples,sr)
        self.assertGreater(np.max(env[(times>1)&(times<1.2)]),.8)
        self.assertLess(np.max(env[(times>2)&(times<2.2)]),.05)
    def test_silent_audio(self):
        env,times=bass_envelope(np.zeros(8000),8000)
        np.testing.assert_array_equal(pulse_positions(env,times,24,24),0)
    def test_export_preserves_other_settings(self):
        cfg,tracks,_=load_config(Path(__file__).parent/'examples'/'working_baseline.txt',True)
        with tempfile.TemporaryDirectory() as tmp:
            out=export_config(cfg,tracks,24,Path(tmp)/'out.txt')
            reloaded,motion,_=load_config(Path(tmp)/'out.txt')
        for key,value in cfg.items():
            if key not in AXES and key not in ('fps','max_frames'):self.assertEqual(out[key],value,key)
        np.testing.assert_allclose(motion,tracks,atol=1e-8)
    def test_fps_change(self):
        x=np.ones((240,6))*.1;y=resample(x,24,480,48)
        self.assertAlmostEqual(y[10,0],.05)
    def test_stick_deadzone(self):
        self.assertEqual(curve(.1),0);self.assertAlmostEqual(curve(1),1)
        self.assertAlmostEqual(curve(-.5),-curve(.5));self.assertLess(curve(.5),.5)

if __name__=='__main__':unittest.main()

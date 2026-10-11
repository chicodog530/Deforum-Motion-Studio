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
        # Export writes reduced keyframes (error-bounded at 1e-4, far below the
        # preview's own +-1/32 px sampler quantization), not every frame.
        np.testing.assert_allclose(motion,tracks,atol=1e-3)
    def test_keyframe_reduction(self):
        n=800
        self.assertEqual(keyframe_indices(np.full(n,2.5)),[0,n-1])
        self.assertEqual(keyframe_indices(np.linspace(0,1,n)),[0,n-1])
        t=np.arange(n);sine=np.sin(2*np.pi*t/192)*.15
        idx=keyframe_indices(sine)
        self.assertLess(len(idx),n)
        self.assertEqual(idx[0],0);self.assertEqual(idx[-1],n-1)
        rec=np.interp(t,idx,sine[idx])
        self.assertLess(np.max(np.abs(rec-sine)),1e-4+1e-9)
        self.assertEqual(keyframe_indices(np.array([1.,2.])),[0,1])
    def test_combine_multi_axes(self):
        p_z=np.zeros(120);p_z[10:20]=1.
        p_y=np.zeros(120);p_y[15:25]=.5
        out,scales,clipped=combine_multi(np.zeros((120,6)),0.,[(2,p_z),(4,p_y)],[.3,.3,10,.3,.3,.3])
        self.assertFalse(clipped)
        self.assertGreater(np.max(np.abs(out[:,2])),0)
        self.assertGreater(np.max(np.abs(out[:,4])),0)
        self.assertAlmostEqual(np.max(np.abs(out[:,0])),0)
        self.assertAlmostEqual(np.sum(out[:,2]),0)  # pulse returns to path
    def test_combine_multi_overlap_sums(self):
        p1=np.zeros(60);p1[10:20]=1.
        p2=np.zeros(60);p2[10:20]=.5
        both,_ ,_=combine_multi(np.zeros((60,6)),0.,[(2,p1),(2,p2)],[.3,.3,10,.3,.3,.3])
        single,_,_=combine_multi(np.zeros((60,6)),0.,[(2,p1+p2)],[.3,.3,10,.3,.3,.3])
        np.testing.assert_allclose(both,single,atol=1e-12)
    def test_combine_multi_per_axis_headroom(self):
        p=np.zeros(40);p[5:15]=2.
        out,scales,_=combine_multi(np.zeros((40,6)),0.,[(4,p)],[float(x) for x in LIMITS])
        self.assertLess(scales[4],1.)
        self.assertLessEqual(np.max(np.abs(out[:,4])),LIMITS[4]+1e-9)
        self.assertNotIn(2,scales)  # untouched axis gets no scale entry
    def test_channel_window(self):
        env=np.zeros(500);env[100]=1.;times=np.arange(500)/100
        ch=validate_channel(dict(DEFAULT_CHANNELS[0],start=1.,end=3.))
        pos=channel_pulse(env,times,ch,120,24)
        nz=np.nonzero(pos)[0]
        self.assertGreaterEqual(nz.min(),20)  # nothing before the window
        self.assertLess(nz.max(),76)          # nothing after the window
        self.assertGreater(np.max(pos),0)
        full=channel_pulse(env,times,validate_channel(dict(DEFAULT_CHANNELS[0])),120,24)
        self.assertGreater(np.max(full),0)
    def test_validate_channel(self):
        ch=validate_channel(dict(DEFAULT_CHANNELS[1]))
        self.assertEqual(ch['axis'],4)
        for bad in ({'mode':'nope'},{'low':5000,'high':100,'mode':'Beat hits'},
                    {'axis':9,'mode':'Beat hits'},{'start':5.,'end':2.,'mode':'Beat hits'}):
            with self.assertRaises(ValueError):validate_channel(dict(DEFAULT_CHANNELS[0],**bad))
    def test_fps_change(self):
        x=np.ones((240,6))*.1;y=resample(x,24,480,48)
        self.assertAlmostEqual(y[10,0],.05)
    def test_stick_deadzone(self):
        self.assertEqual(curve(.1),0);self.assertAlmostEqual(curve(1),1)
        self.assertAlmostEqual(curve(-.5),-curve(.5));self.assertLess(curve(.5),.5)
    def test_spectrum_db(self):
        sr=22050;t=np.arange(sr*2)/sr
        x=np.sin(2*np.pi*440*t)*np.exp(-t)+.01*np.random.default_rng(0).standard_normal(len(t))
        f,db=spectrum_db(x,sr)
        self.assertEqual(len(f),160)
        self.assertLessEqual(db.max(),0.);self.assertGreater(db.max(),-3.)  # log-grid peak ≈ true peak
        peak=f[np.argmax(db)]
        self.assertLess(abs(peak-440),60)  # log grid resolution near 440 Hz
        self.assertTrue(np.all(np.diff(np.log10(f))>0))
    def test_freq_class(self):
        self.assertEqual(freq_class(60),'bass');self.assertEqual(freq_class(249),'bass')
        self.assertEqual(freq_class(250),'mid');self.assertEqual(freq_class(1000),'mid')
        self.assertEqual(freq_class(2000),'high');self.assertEqual(freq_class(5000),'high')
    def test_new_presets(self):
        for name in ('Reveal drift','Fly-through'):
            p=preset(name,240,24,.5,8,1)
            self.assertEqual(p.shape,(240,6))
            self.assertTrue(np.all(np.isfinite(p)))
            self.assertLess(np.max(np.abs(p[0])),1e-6)
            self.assertLess(np.max(np.abs(p[-1])),1e-6)
            self.assertGreater(np.max(np.abs(p)),0.01)
        r=preset('Reveal drift',240,24,.5,8,1)
        self.assertGreater(r[12,2],0)  # starts close (push in)
        f=preset('Fly-through',240,24,.5,8,1)
        self.assertGreater(np.mean(f[60:180,2]),0)  # sustained forward push
    def test_ripple_mode(self):
        env=np.zeros(200);env[50]=1.0;env[120]=0.8
        times=np.arange(200)/100
        pos=pulse_positions(env,times,240,24,strength=2,attack=.05,release=.5,
                            threshold=.35,offset=0,mode='Ripple',min_gap=.18)
        self.assertEqual(len(pos),240)
        self.assertLess(pos.min(),-0.1)   # oscillates both ways
        self.assertGreater(pos.max(),0.5)  # punches out first
        seg=np.abs(pos[12:60])
        self.assertGreater(np.max(seg[:15]),np.max(seg[30:]))  # decays

if __name__=='__main__':unittest.main()

import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
from motion import *
from controller import (curve,Controller,default_pad_map,validate_pad_map,
                        pad_axis_target,button_action)
from video_review import VideoReview

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
    def test_high_cutoff_nyquist_clamp(self):
        # Screenshot case: 8800/11050 must not error; high clamps under Nyquist.
        ch=validate_channel(dict(DEFAULT_CHANNELS[0],name='Crash',low=8800,high=11050))
        self.assertLess(ch['high'],11025)
        self.assertLess(ch['low'],ch['high'])
        env,times=bass_envelope(np.random.default_rng(0).standard_normal(22050),22050,
                                ch['low'],ch['high'])
        self.assertGreater(len(env),0)

class VideoReviewTests(unittest.TestCase):
    def _make(self,path,n=60,fps=24):
        import cv2
        vw=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),fps,(96,64))
        for i in range(n):
            vw.write(np.full((64,96,3),(i*3%256,0,0),np.uint8))  # index in blue
        vw.release()
    def test_sequential_and_seek(self):
        import cv2
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'rev.mp4';self._make(p)
            v=VideoReview(p)
            try:
                self.assertEqual(v.n,60);self.assertAlmostEqual(v.fps,24)
                self.assertEqual(v.frame(0).shape,(64,96,3))
                def blue(i):return float(v.frame(i)[...,2].mean())
                self.assertLess(abs(blue(0)-0),25)
                self.assertLess(abs(blue(30)-90),25)   # seek lands right
                self.assertLess(abs(blue(29)-87),25)   # seek back works
                self.assertLess(abs(blue(31)-93),25)   # sequential fast path
                self.assertLess(abs(blue(59)-177),25)
                self.assertLess(abs(blue(999)-177),25) # clamped to last
            finally:v.close()
    def test_bad_path(self):
        with self.assertRaises(ValueError):VideoReview('/nonexistent/x.mp4')
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

class PadMapTests(unittest.TestCase):
    def test_read_raw_keys(self):
        r=Controller().read_raw()
        for k in ('lx','ly','rx','ry','lt','rt','buttons','label'):
            self.assertIn(k,r)
    def test_default_map(self):
        m=default_pad_map([.3,.3,1.75,.3,.3,.3])
        self.assertEqual(m['lx'],{'target':'axis:0','max':.3,'invert':False})
        self.assertEqual(m['lt']['target'],'axis:2');self.assertTrue(m['lt']['invert'])
        self.assertFalse(m['rt']['invert'])
        self.assertEqual(m['btn_lb'],{'target':'axis:5','max':.3,'invert':True})
        self.assertEqual(m['btn_rb']['target'],'axis:5')
        self.assertEqual(button_action(m,'a'),'record')
        self.assertEqual(button_action(m,'b'),'stop')
        self.assertEqual(button_action(m,'x'),'undo')
        self.assertEqual(button_action(m,'y'),'')
        self.assertEqual(button_action(m,'lb'),'')
    def test_pad_axis_target(self):
        m=default_pad_map([.3,.3,1.75,.3,.3,.3])
        t=pad_axis_target({'lx':1.,'ly':0.,'rx':0.,'ry':0.,'lt':0.,'rt':1.},m,.15)
        self.assertAlmostEqual(t[0],.3)    # full left stick X -> its max
        self.assertAlmostEqual(t[2],1.75)  # full right trigger
        t=pad_axis_target({'lx':0.,'ly':0.,'rx':0.,'ry':0.,'lt':1.,'rt':0.},m,.15)
        self.assertAlmostEqual(t[2],-1.75) # inverted left trigger pulls back
        t=pad_axis_target({'lx':.05,'ly':0.,'rx':0.,'ry':0.,'lt':0.,'rt':0.},m,.15)
        self.assertAlmostEqual(t[0],0.)    # inside dead zone
        m2={'lx':{'target':'axis:1','max':.5,'invert':False}}
        t=pad_axis_target({'lx':1.,'ly':0.,'rx':0.,'ry':0.,'lt':0.,'rt':0.},m2,.15)
        self.assertAlmostEqual(t[1],.5);self.assertAlmostEqual(t[0],0.)
    def test_response_curve(self):
        m={'lx':{'target':'axis:0','max':1.,'invert':False}}
        raw={'lx':.5,'ly':0.,'rx':0.,'ry':0.,'lt':0.,'rt':0.}
        lin=pad_axis_target(raw,m,.15,power=1.)[0]
        self.assertAlmostEqual(lin,(.5-.15)/.85)          # linear past dead zone
        soft=pad_axis_target(raw,m,.15,power=1.7)[0]
        self.assertLess(soft,lin)                          # expo softens mid-range
        full=pad_axis_target(dict(raw,lx=1.),m,.15,power=1.7)[0]
        self.assertAlmostEqual(full,1.)                    # full deflection unaffected
    def test_button_axis(self):
        m={'btn_rb':{'target':'axis:5','max':.2,'invert':False},
           'btn_lb':{'target':'axis:5','max':.2,'invert':True}}
        base={'lx':0.,'ly':0.,'rx':0.,'ry':0.,'lt':0.,'rt':0.,'buttons':0}
        t=pad_axis_target(dict(base,buttons=0x200),m,.15)
        self.assertAlmostEqual(t[5],.2)    # RB held -> full max
        t=pad_axis_target(dict(base,buttons=0x100),m,.15)
        self.assertAlmostEqual(t[5],-.2)   # LB held -> inverted
        t=pad_axis_target(base,m,.15)
        self.assertAlmostEqual(t[5],0.)    # released -> nothing
    def test_validate_repairs(self):
        bad={'lx':{'target':'axis:9','max':'junk','invert':1},
             'btn_a':{'target':'action:fly'},'nope':1}
        m=validate_pad_map(bad,[.3,.3,1.75,.3,.3,.3])
        self.assertEqual(m['lx']['target'],'')
        self.assertEqual(m['lx']['max'],0.)
        self.assertEqual(m['btn_a']['target'],'')
        self.assertIn('btn_b',m)  # missing entries filled from defaults
        self.assertEqual(validate_pad_map(None,[.3]*6)['lx']['target'],'axis:0')

if __name__=='__main__':unittest.main()

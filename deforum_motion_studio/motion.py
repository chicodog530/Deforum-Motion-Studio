"""Frame-based motion engine; no Forge installation is required."""
import ast
import copy
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
from scipy import signal

AXES = ('translation_x', 'translation_y', 'translation_z',
        'rotation_3d_x', 'rotation_3d_y', 'rotation_3d_z')
LABELS = ('Move sideways', 'Move vertically', 'Travel / distance',
          'Tilt up / down', 'Turn left / right', 'Roll')
LIMITS = np.array([.3, .3, 1.75, .3, .3, .3])
PRESETS = ('Still', 'Gentle drift', 'Wave', 'Spiral', 'Figure eight',
           'Rocking', 'Push in', 'Pull out', 'Reveal drift', 'Fly-through')


def expression(source, t):
    """Evaluate a small arithmetic grammar, never Python eval."""
    funcs = {'sin': np.sin, 'cos': np.cos, 'tan': np.tan, 'abs': np.abs,
             'sqrt': np.sqrt, 'exp': np.exp, 'log': np.log}
    ops = {ast.Add: lambda a,b:a+b, ast.Sub: lambda a,b:a-b,
           ast.Mult: lambda a,b:a*b, ast.Div: lambda a,b:a/b,
           ast.Pow: lambda a,b:a**b, ast.Mod: lambda a,b:a%b}
    tree = ast.parse(source.strip(), mode='eval')
    if len(list(ast.walk(tree))) > 100:
        raise ValueError('Expression is too complex')
    def run(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if abs(node.value) > 1e8: raise ValueError('Constant is too large')
            return node.value
        if isinstance(node, ast.Name) and node.id in ('t', 'pi'):
            return t if node.id == 't' else np.pi
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            a,b = run(node.left),run(node.right)
            if isinstance(node.op, ast.Pow) and np.any(np.abs(b)>16):
                raise ValueError('Exponent is too large')
            return ops[type(node.op)](a,b)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            return run(node.operand) * (-1 if isinstance(node.op,ast.USub) else 1)
        if isinstance(node, ast.Call) and isinstance(node.func,ast.Name) and node.func.id in funcs and len(node.args)==1 and not node.keywords:
            return funcs[node.func.id](run(node.args[0]))
        raise ValueError('Unsupported expression; use numeric keyframes or sin/cos arithmetic')
    try:
        with np.errstate(all='ignore'):
            result = np.asarray(run(tree.body), dtype=float)
    except (ArithmeticError,OverflowError) as e:
        raise ValueError('Invalid arithmetic in schedule: '+str(e)) from e
    if not np.all(np.isfinite(result)): raise ValueError('Expression produces non-finite values')
    return result


def parse_schedule(text, frames, repair=False):
    """Validate complete schedule; preserve interpolation towards future keys."""
    original = text
    if repair: text = re.sub(r'\)\s*(?=\d+\s*:)', '),', text)
    entries, pos = [], 0
    while pos < len(text):
        m = re.match(r'\s*(\d+)\s*:\s*\(', text[pos:])
        if not m: raise ValueError(f'Invalid schedule near {text[pos:pos+35]!r}')
        frame = int(m.group(1)); start = pos+m.end(); end=start; depth=1
        while end<len(text) and depth:
            if text[end]=='(': depth+=1
            elif text[end]==')': depth-=1
            end+=1
        if depth: raise ValueError('Unclosed schedule parentheses')
        expr=text[start:end-1]
        entries.append((frame,expr)); pos=end
        tail=re.match(r'\s*(,|$)',text[pos:])
        if not tail: raise ValueError('Missing comma between keyframes')
        pos+=tail.end()
        if tail.group(1)==',' and not text[pos:].strip(): raise ValueError('Trailing comma')
    if not entries or entries[0][0]!=0: raise ValueError('Schedule must begin at frame 0')
    keys=[x[0] for x in entries]
    if any(b<=a for a,b in zip(keys,keys[1:])): raise ValueError('Keyframes must increase, without duplicates')
    t=np.arange(frames,dtype=float)
    # Numeric schedules interpolate. Expressions are evaluated for each frame
    # in their active segment, matching Deforum's common expression schedules.
    numeric=[]
    for f,e in entries:
        v=expression(e,t)
        numeric.append(float(v) if v.ndim==0 else None)
    if all(x is not None for x in numeric):
        result=np.interp(t,keys,numeric)
    else:
        result=np.zeros(frames)
        for i,(f,e) in enumerate(entries):
            stop=entries[i+1][0] if i+1<len(entries) else frames
            active=(t>=f)&(t<stop)
            v=expression(e,t[active]); result[active]=v
    return result, text != original


def load_config(path, repair=False):
    data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict): raise ValueError('Settings must be a JSON object')
    if data.get('animation_mode')!='3D': raise ValueError('This initial version supports 3D Deforum settings')
    n=int(data.get('max_frames',800)); fps=float(data.get('fps',24))
    if not 2<=n<=30000 or not 1<=fps<=120: raise ValueError('Use 2–30,000 frames and 1–120 FPS')
    tracks=[]; repairs=[]
    for axis in AXES:
        values,changed=parse_schedule(str(data.get(axis,'0:(0)')),n,repair)
        tracks.append(values)
        if changed: repairs.append(axis)
    return data,np.array(tracks).T,repairs


def preset(name, frames, fps, strength=.5, cycle=8, fade=1):
    t=np.arange(frames)/fps; phase=2*np.pi*t/max(.2,cycle)
    out=np.zeros((frames,6)); a=.3*strength; z=1.75*strength
    if name=='Gentle drift': out[:,0]=a*.4;out[:,1]=a*.2;out[:,4]=a*.2*np.sin(phase)
    elif name=='Wave': out[:,0]=a*np.sin(phase);out[:,1]=a*.4*np.cos(phase);out[:,4]=a*.5*np.sin(phase)
    elif name=='Spiral': out[:,0]=a*.5*np.cos(phase);out[:,1]=a*.5*np.sin(phase);out[:,5]=a*.6;out[:,2]=z*.25
    elif name=='Figure eight': out[:,0]=a*np.sin(phase);out[:,1]=a*np.sin(2*phase)
    elif name=='Rocking': out[:,3]=a*.5*np.sin(phase);out[:,5]=a*np.sin(phase)
    elif name=='Push in': out[:,2]=z
    elif name=='Pull out': out[:,2]=-z
    elif name=='Reveal drift':
        # Close-up, pull back to reveal while drifting sideways, push back in.
        out[:,2]=z*.8*np.cos(phase);out[:,0]=a*np.sin(phase);out[:,1]=a*.25*np.cos(phase)
    elif name=='Fly-through':
        # Steady forward push with a continuous lateral weave and gentle bob.
        out[:,2]=z*.55;out[:,0]=a*np.sin(phase);out[:,1]=a*.3*np.sin(2*phase+1)
    elif name!='Still': raise ValueError('Unknown preset')
    if fade>0:
        env=np.minimum(np.clip(t/fade,0,1),np.clip((t[-1]-t)/fade,0,1))
        out*= (.5-.5*np.cos(np.pi*env))[:,None]
    return out


def decode_audio(path, max_seconds, sample_rate=None):
    if sample_rate is None:sample_rate=CHANNEL_SR
    executable=shutil.which('ffmpeg')
    if not executable:
        import imageio_ffmpeg
        executable=imageio_ffmpeg.get_ffmpeg_exe()
    # Mono float PCM for analysis; source file is never changed.
    try:
        result=subprocess.run([executable,'-v','error','-i',str(path),'-t',str(max_seconds),
            '-f','f32le','-ac','1','-ar',str(int(sample_rate)),'pipe:1'],capture_output=True,check=True)
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode('utf-8', 'ignore').strip()
        raise ValueError(f'Audio decoding failed: {err or str(e)}') from e
    samples=np.frombuffer(result.stdout,dtype='<f4').copy()
    if samples.size<200: raise ValueError('Soundtrack is empty or too short')
    return samples,int(sample_rate)


def bass_envelope(samples, sr, low=35, high=180):
    if not 10<=low<high<sr/2: raise ValueError('Invalid bass frequency range')
    filtered=signal.sosfiltfilt(signal.butter(4,[low,high],btype='bandpass',fs=sr,output='sos'),samples)
    hop=max(1,round(sr/100)); size=len(filtered)//hop
    if size<3: raise ValueError('Audio is too short')
    env=np.sqrt(np.mean(filtered[:size*hop].reshape(size,hop)**2,axis=1))
    norm=np.percentile(env,95)
    env=np.clip(env/max(norm,1e-8),0,1)
    return env, np.arange(size)*hop/sr


def pulse_positions(env, times, frames, fps, strength=2, attack=.08, release=.22,
                    threshold=.35, offset=0, mode='Beat hits', min_gap=.18):
    t=np.arange(frames)/fps; position=np.zeros(frames)
    if mode=='Bass intensity':
        smooth=signal.sosfilt(signal.butter(2,6,fs=100,output='sos'),env)
        position=np.interp(t-offset,times,smooth,left=0,right=0)*strength
    elif mode=='Ripple':
        # Waterdrop ripple: each detected hit launches a decaying oscillation
        # (one wobble per release time constant, ~3-4 visible rings).
        peaks,_=signal.find_peaks(env,height=threshold,prominence=.08,
                                  distance=max(1,round(min_gap*100)))
        tau=max(release,.01)
        for i in peaks:
            t0=times[i]+offset
            dur=int(round(4*tau*fps))
            if dur<2:continue
            tt=np.arange(dur)/fps
            wave=np.exp(-tt/tau)*np.cos(2*np.pi*tt/tau)
            f0=int(round(t0*fps));f1=min(frames,f0+dur)
            if f1>f0:position[f0:f1]+=wave[:f1-f0]*env[i]*strength
    else:
        peaks,_=signal.find_peaks(env,height=threshold,prominence=.08,
                                  distance=max(1,round(min_gap*100)))
        for i in peaks:
            # Peak coincides with bass hit; onset ramps just before it.
            delta=t-(times[i]+offset)
            shape=np.zeros(frames)
            rising=(delta>=-attack)&(delta<=0)
            falling=(delta>0)&(delta<release)
            shape[rising]=.5+.5*np.cos(np.pi*delta[rising]/max(attack,.001))
            shape[falling]=.5+.5*np.cos(np.pi*delta[falling]/max(release,.001))
            position=np.maximum(position,shape*env[i]*strength)
    # Begin/end at baseline, including pulses cut by the video boundary.
    taper=max(2,round(.15*fps)); taper=min(taper,frames//2)
    ramp=np.linspace(0,1,taper)
    position[:taper]*=ramp;position[-taper:]*=ramp[::-1]
    position[0]=0;position[-1]=0
    return position


def spectrum_db(samples, sr, n_fft=4096, bins=160, fmin=25, fmax=None):
    """Peak-hold magnitude spectrum on a log frequency grid, in dB.

    Returns (freqs, db) with 0 dB at the loudest bin. Peak-hold (max over
    time) shows where transient hits actually live, which is what the
    detection channels trigger on.
    """
    fmax = fmax or sr/2
    freqs, _, Z = signal.stft(samples, fs=sr, nperseg=n_fft,
                              noverlap=n_fft*3//4, window='hann',
                              padded=False, boundary=None)
    mag = np.maximum(np.max(np.abs(Z), axis=1), 1e-9)
    db = 20*np.log10(mag)
    db = db - db.max()
    grid = np.logspace(np.log10(fmin), np.log10(min(fmax, sr/2)), bins)
    return grid, np.interp(grid, freqs, db)


def freq_class(f):
    """Rough bass/mid/high classification for the spectrum readout."""
    f=float(f)
    if f<250:return 'bass'
    if f<2000:return 'mid'
    return 'high'


# ---------------------------------------------------------------------------
# Sound channels: each channel watches one frequency band and drives one
# motion axis with its own pulse shape. Channels may overlap in time.
# ---------------------------------------------------------------------------
CHANNEL_MODES=('Beat hits','Bass intensity','Ripple')
DEFAULT_CHANNELS=(
    {'name':'Bass','enabled':True,'axis':2,'mode':'Beat hits',
     'low':35,'high':180,'strength':2.0,'attack':0.08,'release':0.22,
     'threshold':0.35,'offset':0.0,'min_gap':0.18,'start':0.0,'end':0.0},
    {'name':'Snare','enabled':False,'axis':4,'mode':'Beat hits',
     'low':1500,'high':6000,'strength':1.0,'attack':0.03,'release':0.12,
     'threshold':0.4,'offset':0.0,'min_gap':0.10,'start':0.0,'end':0.0},
    {'name':'Crash','enabled':False,'axis':5,'mode':'Bass intensity',
     'low':6000,'high':11000,'strength':0.8,'attack':0.05,'release':0.4,
     'threshold':0.3,'offset':0.0,'min_gap':0.25,'start':0.0,'end':0.0},
)
CHANNEL_SR=22050  # decode rate for channel analysis; covers crash cymbals


def validate_channel(ch):
    """Return a cleaned copy of a channel dict; raise ValueError if invalid."""
    ch=dict(ch)
    ch['name']=str(ch.get('name','Channel'))[:32] or 'Channel'
    ch['enabled']=bool(ch.get('enabled',True))
    ch['axis']=int(ch.get('axis',2))
    if not 0<=ch['axis']<6: raise ValueError('Channel axis must be 0-5')
    if ch.get('mode') not in CHANNEL_MODES: raise ValueError('Unknown channel mode')
    bounds={'low':(10,20000),'high':(20,22050),'strength':(0,30),
            'attack':(0.005,5),'release':(0.01,5),'threshold':(0.01,1),
            'offset':(-30,30),'min_gap':(0.02,5),'start':(0,86400),'end':(0,86400)}
    for key,(lo,hi) in bounds.items():
        try: v=float(ch.get(key,0))
        except (TypeError,ValueError): raise ValueError(f"Channel '{ch['name']}': {key} must be a number")
        if not np.isfinite(v): raise ValueError(f"Channel '{ch['name']}': {key} must be finite")
        ch[key]=min(hi,max(lo,v))
    if not ch['low']<ch['high']:
        raise ValueError(f"Channel '{ch['name']}': low cutoff must be below high cutoff")
    if ch['end']!=0 and not ch['end']>ch['start']:
        raise ValueError(f"Channel '{ch['name']}': end must be after start (or 0 for full video)")
    return ch


def channel_pulse(env, times, channel, frames, fps):
    """Pulse array for one channel, windowed to its start/end range.

    A short cosine taper at window edges avoids motion clicks where a
    channel starts or stops mid-video. end==0 means the full video.
    """
    pos=pulse_positions(env,times,frames,fps,channel['strength'],
                        channel['attack'],channel['release'],
                        channel['threshold'],channel['offset'],
                        channel['mode'],channel['min_gap'])
    a=int(round(channel['start']*fps))
    b=frames if channel['end']==0 else int(round(channel['end']*fps))
    a,b=max(0,a),min(frames,b)
    if b<=a: return np.zeros(frames)
    window=np.zeros(frames); window[a:b]=1.0
    taper_len=min(int(round(0.1*fps)),max(1,(b-a)//4))
    if taper_len>1:
        ramp=0.5-0.5*np.cos(np.pi*np.arange(taper_len)/taper_len)
        window[a:a+taper_len]*=ramp
        window[b-taper_len:b]*=ramp[::-1]
    return pos*window


def combine_multi(base, travel, pulses, limits):
    """Sum travel plus any number of returning pulses on any axes.

    pulses: iterable of (axis_index, pulse_array). Pulses targeting the same
    axis are summed (channels may overlap); each axis then gets the uniform
    headroom scaling treatment so no axis exceeds its limit.
    Returns (output_tracks, scales_by_axis, clipped_flag).
    """
    limits=np.asarray(limits,float)
    if limits.shape!=(6,) or not np.all(np.isfinite(limits)) or np.any(limits<=0):
        raise ValueError('Limits must be six positive finite numbers')
    base=np.asarray(base,float)
    if base.ndim!=2 or base.shape[1]!=6 or not np.all(np.isfinite(base)):
        raise ValueError('Base motion must be a finite (frames, 6) array')
    n=len(base)
    out=np.clip(base,-limits,limits)
    clipped=bool(np.any(np.abs(base)>limits+1e-8))
    # Continuous travel applies to Z.
    z_pre=out[:,2]+travel
    if np.any(np.abs(z_pre)>limits[2]+1e-8): clipped=True
    out[:,2]=np.clip(z_pre,-limits[2],limits[2])
    by_axis={}
    for axis,pos in pulses:
        axis=int(axis)
        if not 0<=axis<6: raise ValueError('Pulse axis must be 0-5')
        pos=np.asarray(pos,float)
        if pos.shape!=(n,) or not np.all(np.isfinite(pos)):
            raise ValueError('Pulse must be a finite per-frame array')
        by_axis.setdefault(axis,[]).append(pos)
    scales={}
    for axis,arrs in by_axis.items():
        # Pulse shape is a position offset; its derivative is the per-frame
        # increment, which integrates back to zero: the camera returns to
        # the underlying path instead of just stopping.
        velocity=np.diff(np.sum(arrs,axis=0),prepend=0)
        velocity[0]=0.
        current=out[:,axis]; lim=limits[axis]; scale=1.
        with np.errstate(divide='ignore',invalid='ignore'):
            up=velocity>0; down=velocity<0
            if np.any(up): scale=min(scale,float(np.min((lim-current[up])/velocity[up])))
            if np.any(down): scale=min(scale,float(np.min((-lim-current[down])/velocity[down])))
        scale=max(0.,min(1.,scale))
        out[:,axis]=current+scale*velocity
        scales[axis]=scale
    return out,scales,clipped


def combine(base, travel, pulse, limits):
    """Legacy single-pulse wrapper: one pulse on the Z (travel) axis."""
    out,scales,clipped=combine_multi(base,travel,[(2,np.asarray(pulse,float))],limits)
    return out,scales.get(2,1.),clipped


def keyframe_indices(values, tol=1e-4):
    """Greedy error-bounded keyframe reduction.

    Returns frame indices such that linear interpolation between them
    reproduces `values` within `tol`. Deforum interpolates linearly, so the
    exported schedule matches the computed tracks to this tolerance.
    tol=1e-4 is ~0.0002 px for translation (unit = 1/200 frame) and 1e-4
    degrees for rotation: far below the preview's own +-1/32 px sampler
    quantization, i.e. visually and geometrically lossless.
    """
    values=np.asarray(values,float); n=len(values)
    if n<=2: return [0,n-1]
    def ok(s,e):
        L=e-s
        if L<2: return True
        seg=values[s:e+1]
        interp=values[s]+(values[e]-values[s])*np.arange(L+1)/L
        return bool(np.max(np.abs(interp-seg))<=tol)
    idx=[0]; start=0
    while start<n-1:
        lo,hi=start+1,n-1
        while lo<hi:
            mid=(lo+hi+1)//2
            if ok(start,mid): lo=mid
            else: hi=mid-1
        idx.append(lo); start=lo
    return idx


def export_config(config, tracks, fps, path, keyframe_tol=1e-4):
    if tracks.ndim!=2 or tracks.shape[1]!=6 or not np.all(np.isfinite(tracks)):
        raise ValueError('Invalid motion tracks')
    out=copy.deepcopy(config)
    out['max_frames']=len(tracks);out['fps']=fps
    for i,axis in enumerate(AXES):
        # Reduced keyframes: Deforum linearly interpolates, so only write
        # keys needed to reproduce the track within tolerance.
        idx=keyframe_indices(tracks[:,i],keyframe_tol)
        out[axis]=','.join(f'{f}:({tracks[f,i]:.8f})' for f in idx)
    Path(path).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    return out


def resample(tracks, old_fps, frames, fps):
    old=np.arange(len(tracks))/old_fps;new=np.arange(frames)/fps
    # Keep motion speed per SECOND when changing FPS.
    return np.column_stack([np.interp(new,old,tracks[:,i],right=0)*old_fps/fps for i in range(6)])

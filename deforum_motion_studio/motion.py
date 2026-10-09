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
           'Rocking', 'Push in', 'Pull out')


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
    elif name!='Still': raise ValueError('Unknown preset')
    if fade>0:
        env=np.minimum(np.clip(t/fade,0,1),np.clip((t[-1]-t)/fade,0,1))
        out*= (.5-.5*np.cos(np.pi*env))[:,None]
    return out


def decode_audio(path, max_seconds):
    executable=shutil.which('ffmpeg')
    if not executable:
        import imageio_ffmpeg
        executable=imageio_ffmpeg.get_ffmpeg_exe()
    # Mono float PCM for analysis; source file is never changed.
    try:
        result=subprocess.run([executable,'-v','error','-i',str(path),'-t',str(max_seconds),
            '-f','f32le','-ac','1','-ar','8000','pipe:1'],capture_output=True,check=True)
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode('utf-8', 'ignore').strip()
        raise ValueError(f'Audio decoding failed: {err or str(e)}') from e
    samples=np.frombuffer(result.stdout,dtype='<f4').copy()
    if samples.size<200: raise ValueError('Soundtrack is empty or too short')
    return samples,8000


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
                    threshold=.35, offset=0, mode='Beat hits'):
    t=np.arange(frames)/fps; position=np.zeros(frames)
    if mode=='Bass intensity':
        smooth=signal.sosfilt(signal.butter(2,6,fs=100,output='sos'),env)
        position=np.interp(t-offset,times,smooth,left=0,right=0)*strength
    else:
        peaks,_=signal.find_peaks(env,height=threshold,prominence=.08,distance=18)
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


def combine(base, travel, pulse, limits):
    """Sum travel with a returning pulse; scale pulse uniformly if headroom is low."""
    limits=np.asarray(limits,float)
    if limits.shape!=(6,) or not np.all(np.isfinite(limits)) or np.any(limits<=0):
        raise ValueError('Limits must be six positive finite numbers')
    out=np.clip(np.asarray(base,float),-limits,limits)
    original=out[:,2]+travel
    z=np.clip(original,-limits[2],limits[2])
    velocity=np.diff(pulse,prepend=pulse[0])
    scale=1.
    for v, current in zip(velocity,z):
        if v>0: scale=min(scale,(limits[2]-current)/v)
        elif v<0: scale=min(scale,(-limits[2]-current)/v)
    scale=max(0.,min(1.,scale))
    out[:,2]=z+scale*velocity
    clipped=bool(np.any(np.abs(base)>limits+1e-8) or np.any(np.abs(original)>limits[2]+1e-8))
    return out,scale,clipped


def export_config(config, tracks, fps, path):
    if tracks.ndim!=2 or tracks.shape[1]!=6 or not np.all(np.isfinite(tracks)):
        raise ValueError('Invalid motion tracks')
    out=copy.deepcopy(config)
    out['max_frames']=len(tracks);out['fps']=fps
    for i,axis in enumerate(AXES):
        out[axis]=','.join(f'{f}:({v:.8f})' for f,v in enumerate(tracks[:,i]))
    Path(path).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    return out


def resample(tracks, old_fps, frames, fps):
    old=np.arange(len(tracks))/old_fps;new=np.arange(frames)/fps
    # Keep motion speed per SECOND when changing FPS.
    return np.column_stack([np.interp(new,old,tracks[:,i],right=0)*old_fps/fps for i in range(6)])

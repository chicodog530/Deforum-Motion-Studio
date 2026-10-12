"""Native Xbox/XInput reader on Windows, gracefully unavailable elsewhere.

Physical inputs are exposed raw (read_raw) so the app can map them freely:
sticks/triggers report deflection, buttons report a bitmask. The mapping
lives in the app (Gamepad tab) and is stored per user in settings.json.
"""
import ctypes
import sys
import numpy as np

class Gamepad(ctypes.Structure):
    _fields_=[('buttons',ctypes.c_uint16),('left_trigger',ctypes.c_ubyte),
              ('right_trigger',ctypes.c_ubyte),('lx',ctypes.c_int16),
              ('ly',ctypes.c_int16),('rx',ctypes.c_int16),
              ('ry',ctypes.c_int16)]
class State(ctypes.Structure):
    _fields_=[('packet',ctypes.c_uint32),('pad',Gamepad)]

def curve(value,deadzone=.15,power=1.7):
    value=float(np.clip(value,-1,1))
    if abs(value)<=deadzone:return 0.
    return np.sign(value)*((abs(value)-deadzone)/(1-deadzone))**power

# Physical inputs: sticks/triggers are analog, buttons are the XInput bitmask.
STICK_INPUTS=(('lx','Left stick X'),('ly','Left stick Y'),
              ('rx','Right stick X'),('ry','Right stick Y'),
              ('lt','Left trigger'),('rt','Right trigger'))
BUTTON_BITS={'a':0x1000,'b':0x2000,'x':0x4000,'y':0x8000,
             'lb':0x0100,'rb':0x0200,'back':0x0020,'start':0x0010,
             'dup':0x0001,'ddown':0x0002,'dleft':0x0004,'dright':0x0008,
             'lthumb':0x0040,'rthumb':0x0080}
BUTTON_INPUTS=(('a','A'),('b','B'),('x','X'),('y','Y'),
               ('lb','LB'),('rb','RB'),('back','Back'),('start','Start'),
               ('dup','D-pad up'),('ddown','D-pad down'),
               ('dleft','D-pad left'),('dright','D-pad right'),
               ('lthumb','Left thumb'),('rthumb','Right thumb'))
ACTIONS=(('record','Record'),('stop','Stop'),('undo','Undo'),('play','Play'))

def default_pad_map(limits,scale=1.):
    """Factory mapping reproducing the classic fixed layout.

    limits: the 6 default axis speed limits. Each analog input gets its own
    max (movement per second at full deflection); buttons get actions.
    scale=1. means full deflection reaches the full axis limit (the per-axis
    limits still cap the combined total).
    """
    m={}
    for inp,ax in (('lx',0),('ly',1),('rx',4),('ry',3)):
        m[inp]={'target':f'axis:{ax}','max':round(float(limits[ax])*scale,3),'invert':False}
    m['lt']={'target':'axis:2','max':round(float(limits[2])*scale,3),'invert':True}
    m['rt']={'target':'axis:2','max':round(float(limits[2])*scale,3),'invert':False}
    # Bumpers stay digital but can drive an axis while held (classic: roll).
    m['btn_lb']={'target':'axis:5','max':round(float(limits[5])*scale,3),'invert':True}
    m['btn_rb']={'target':'axis:5','max':round(float(limits[5])*scale,3),'invert':False}
    for b,act in (('a','record'),('b','stop'),('x','undo')):
        m['btn_'+b]={'target':f'action:{act}'}
    return m

def validate_pad_map(d,limits):
    """Clean a loaded mapping; unknown inputs dropped, bad values repaired."""
    good=default_pad_map(limits);out={}
    if not isinstance(d,dict):return good
    for inp,_ in STICK_INPUTS:
        cfg=d.get(inp)
        if not isinstance(cfg,dict):continue
        tgt=cfg.get('target','')
        ok_tgt=tgt=='' or (tgt.startswith('axis:') and tgt[5:].isdigit()
                           and 0<=int(tgt[5:])<6)
        try:maxv=max(0.0,min(30.0,float(cfg.get('max',0))))
        except (TypeError,ValueError):maxv=0.0
        out[inp]={'target':tgt if ok_tgt else '',
                  'max':maxv,'invert':bool(cfg.get('invert'))}
    for b,_ in BUTTON_INPUTS:
        cfg=d.get('btn_'+b)
        if not isinstance(cfg,dict):continue
        tgt=cfg.get('target','')
        acts=[f'action:{a}' for a,_ in ACTIONS]
        ok_axis=tgt.startswith('axis:') and tgt[5:].isdigit() and 0<=int(tgt[5:])<6
        if tgt!='' and tgt not in acts and not ok_axis:tgt=''
        try:maxv=max(0.0,min(30.0,float(cfg.get('max',0))))
        except (TypeError,ValueError):maxv=0.0
        out['btn_'+b]={'target':tgt,'max':maxv,'invert':bool(cfg.get('invert'))}
    # Fill anything the file didn't mention with defaults.
    for k,v in good.items():out.setdefault(k,v)
    return out

def pad_axis_target(raw,pad_map,deadzone,power=1.7):
    """Analog contributions per motion axis from the current raw readings.

    raw: dict from Controller.read_raw. Returns a 6-vector in axis units/sec.
    Each assigned stick/trigger contributes curve(deflection)*max; an assigned
    button contributes its full max while held. Inverted if set.
    power=1. is linear past the dead zone; higher values soften the mid-range.
    """
    contrib=np.zeros(6)
    for inp,cfg in pad_map.items():
        if not isinstance(cfg,dict):continue
        tgt=cfg.get('target','')
        if not (isinstance(tgt,str) and tgt.startswith('axis:')):continue
        try:ax=int(tgt[5:])
        except ValueError:continue
        if not 0<=ax<6:continue
        try:maxv=max(0.0,float(cfg.get('max',0)))
        except (TypeError,ValueError):continue
        if maxv<=0:continue
        if inp.startswith('btn_'):
            b=inp[4:]
            if b not in BUTTON_BITS:continue
            c=1.0 if raw.get('buttons',0)&BUTTON_BITS[b] else 0.0
        elif inp in raw:
            dz=.05 if inp in ('lt','rt') else deadzone
            c=curve(raw[inp],dz,power)
        else:continue
        if cfg.get('invert'):c=-c
        contrib[ax]+=c*maxv
    return contrib

def button_action(pad_map,btn):
    """Action id ('record', ...) assigned to a button, or ''."""
    tgt=pad_map.get('btn_'+btn,{})
    tgt=tgt.get('target','') if isinstance(tgt,dict) else ''
    return tgt[7:] if isinstance(tgt,str) and tgt.startswith('action:') else ''

class Controller:
    def __init__(self):
        self.api=None
        if sys.platform=='win32':
            for name in ('xinput1_4.dll','xinput1_3.dll','xinput9_1_0.dll'):
                try:
                    self.api=ctypes.WinDLL(name).XInputGetState
                    self.api.argtypes=[ctypes.c_uint32,ctypes.POINTER(State)]
                    self.api.restype=ctypes.c_uint32
                    break
                except OSError:pass
    def read_raw(self):
        """Raw physical inputs. Sticks in [-1,1], triggers in [0,1].

        Returns dict with keys lx,ly,rx,ry,lt,rt, buttons (bitmask), label.
        """
        blank={k:0.0 for k,_ in STICK_INPUTS}
        blank.update(buttons=0,label='No Xbox controller detected (mouse controls work)')
        if self.api:
            for index in range(4):
                state=State()
                if self.api(index,ctypes.byref(state))==0:
                    p=state.pad
                    return {'lx':p.lx/32768,'ly':p.ly/32768,
                            'rx':p.rx/32768,'ry':p.ry/32768,
                            'lt':p.left_trigger/255,'rt':p.right_trigger/255,
                            'buttons':p.buttons,
                            'label':f'Xbox controller {index+1} connected'}
        return blank
    def read(self,deadzone=.15):
        """Legacy fixed mapping (kept for compatibility)."""
        r=self.read_raw()
        v=[curve(r['lx'],deadzone),curve(r['ly'],deadzone),
           curve(r['rt']-r['lt'],.05),
           curve(r['ry'],deadzone),curve(r['rx'],deadzone),
           float(bool(r['buttons']&0x200))-float(bool(r['buttons']&0x100))]
        return np.array(v),r['buttons'],r['label']

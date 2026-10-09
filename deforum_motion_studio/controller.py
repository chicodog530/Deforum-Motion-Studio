"""Native Xbox/XInput reader on Windows, gracefully unavailable elsewhere."""
import ctypes
import sys
import numpy as np

class Gamepad(ctypes.Structure):
    _fields_=[('buttons',ctypes.c_uint16),('left_trigger',ctypes.c_ubyte),
              ('right_trigger',ctypes.c_ubyte),('lx',ctypes.c_int16),
              ('ly',ctypes.c_int16),('rx',ctypes.c_int16),('ry',ctypes.c_int16)]
class State(ctypes.Structure):
    _fields_=[('packet',ctypes.c_uint32),('pad',Gamepad)]

def curve(value,deadzone=.15):
    value=float(np.clip(value,-1,1))
    if abs(value)<=deadzone:return 0.
    return np.sign(value)*((abs(value)-deadzone)/(1-deadzone))**1.7

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
    def read(self,deadzone=.15):
        if self.api:
            for index in range(4):
                state=State()
                if self.api(index,ctypes.byref(state))==0:
                    p=state.pad
                    v=[curve(p.lx/32768,deadzone),curve(p.ly/32768,deadzone),
                       curve((p.right_trigger-p.left_trigger)/255,.05),
                       curve(p.ry/32768,deadzone),curve(p.rx/32768,deadzone),
                       float(bool(p.buttons&0x200))-float(bool(p.buttons&0x100))]
                    return np.array(v),p.buttons,f'Xbox controller {index+1} connected'
        return np.zeros(6),0,'No Xbox controller detected (mouse controls work)'

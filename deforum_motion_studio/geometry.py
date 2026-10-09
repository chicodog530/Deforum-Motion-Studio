"""Flat-depth camera geometry matched to the user's uploaded animation.py.

Independent NumPy implementation of the documented XYZ row-vector camera
projection and grid_sample coordinate equations. OpenCV performs the pixel
sampling; its interpolation tables round coordinates to 1/32 pixel, unlike
Torch. This affects pixel values slightly, not the intended motion scale.
"""
from collections import OrderedDict
import math
import cv2
import numpy as np
from PIL import Image, ImageDraw
from motion import parse_schedule


def rotation_xyz(degrees):
    x,y,z=np.radians(degrees)
    cx,sx=np.cos(x),np.sin(x);cy,sy=np.cos(y),np.sin(y);cz,sz=np.cos(z),np.sin(z)
    rx=np.array([[1,0,0],[0,cx,-sx],[0,sx,cx]],dtype=np.float32)
    ry=np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]],dtype=np.float32)
    rz=np.array([[cz,-sz,0],[sz,cz,0],[0,0,1]],dtype=np.float32)
    return rx@ry@rz


def sample_map(width,height,movement,fov,aspect):
    """Destination -> source pixel coords, align_corners=False, flat z=1.

    World grid includes endpoints [-1,1]. Image sampling grid uses pixel
    centers instead. Keeping both conventions is essential for parity.
    """
    if not 0<fov<179 or not aspect>0:raise ValueError('Invalid FOV/aspect ratio')
    x=np.linspace(-1,1,width,dtype=np.float32)[None,:]
    y=np.linspace(-1,1,height,dtype=np.float32)[:,None]
    r=rotation_xyz(movement[3:]);tx,ty,tz=np.array(movement[:3],dtype=np.float32)*np.array([-1,1,-1],np.float32)/200
    nx=x*r[0,0]+y*r[1,0]+r[2,0]+tx
    ny=x*r[0,1]+y*r[1,1]+r[2,1]+ty
    nz=x*r[0,2]+y*r[1,2]+r[2,2]+tz
    if np.any(np.abs(nz)<1e-5):raise ValueError('Camera transform crosses the projection plane')
    sy=np.float32(1/math.tan(math.radians(fov)/2));sx=sy/np.float32(aspect)
    offset_x=(nx/nz-x)*sx;offset_y=(ny/nz-y)*sy
    map_x=np.arange(width,dtype=np.float32)[None,:]-offset_x*np.float32(width/2)
    map_y=np.arange(height,dtype=np.float32)[:,None]-offset_y*np.float32(height/2)
    return np.ascontiguousarray(map_x),np.ascontiguousarray(map_y)


def warp(image,movement,fov,aspect,padding='border',sampling='bicubic'):
    if not np.any(movement):return image.copy()
    h,w=image.shape[:2];mx,my=sample_map(w,h,movement,fov,aspect)
    modes={'bilinear':cv2.INTER_LINEAR,'bicubic':cv2.INTER_CUBIC,'nearest':cv2.INTER_NEAREST}
    borders={'border':cv2.BORDER_REPLICATE,'reflection':cv2.BORDER_REFLECT,'zeros':cv2.BORDER_CONSTANT}
    if sampling not in modes or padding not in borders:raise ValueError('Unsupported sampling/padding mode')
    # Torch samples float RGB, clamps, then truncates to uint8 each frame.
    result=cv2.remap(image.astype(np.float32),mx,my,modes[sampling],borderMode=borders[padding])
    return np.clip(result,0,255).astype(np.uint8)


def reference_grid(width,height):
    image=Image.new('RGB',(width,height),'#101923');draw=ImageDraw.Draw(image)
    for x in np.linspace(0,width-1,13):draw.line((x,0,x,height),fill='#476372',width=2)
    for y in np.linspace(0,height-1,17):draw.line((0,y,width,y),fill='#476372',width=2)
    for radius in (.12,.24,.36):
        r=min(width,height)*radius
        draw.ellipse((width/2-r,height/2-r,width/2+r,height/2+r),outline='#48c9b0',width=4)
    draw.rectangle((width*.05,height*.05,width*.95,height*.95),outline='#ffc857',width=4)
    return image


class GeometryPreview:
    def __init__(self,config,tracks,image=None):
        if config.get('use_depth_warping',False):
            raise ValueError('Calibrated preview requires depth warping OFF; actual depth maps are needed to match depth-warped motion.')
        if config.get('enable_perspective_flip',False):
            raise ValueError('Perspective flip is not supported by the calibrated preview.')
        if config.get('shake_name') not in (None,'None','none',''):
            raise ValueError('Shakify must be disabled to match the exported six motion tracks.')
        self.width=int(config.get('W',768));self.height=int(config.get('H',1120))
        if not 16<=self.width<=4096 or not 16<=self.height<=4096:raise ValueError('Preview supports dimensions 16–4096 pixels')
        self.tracks=tracks;self.padding=config.get('padding_mode','border');self.sampling=config.get('sampling_mode','bicubic')
        self.fov=parse_schedule(str(config.get('fov_schedule','0:(70)')),len(tracks))[0]
        self.aspect=np.full(len(tracks),self.width/self.height) if config.get('aspect_ratio_use_old_formula',False) else parse_schedule(str(config.get('aspect_ratio_schedule','0:(1)')),len(tracks))[0]
        if np.any((self.fov<=0)|(self.fov>=179)) or np.any(self.aspect<=0):raise ValueError('Invalid FOV/aspect schedule')
        source=reference_grid(self.width,self.height) if image is None else image.resize((self.width,self.height),Image.Resampling.LANCZOS)
        self.source=np.array(source.convert('RGB'));self.last=0;self.current=self.source.copy()
        self.checkpoints=OrderedDict({0:self.source.copy()})

    def update_tracks(self,tracks):
        if tracks.shape!=self.tracks.shape:raise ValueError('Track size changed; recreate preview')
        changed=np.where(np.any(np.abs(tracks-self.tracks)>1e-10,axis=1))[0]
        self.tracks=tracks.copy()
        if not len(changed):return
        first=int(changed[0])
        for key in list(self.checkpoints):
            if key>=first and key!=0:del self.checkpoints[key]
        if self.last>=first:
            start=max((f for f in self.checkpoints if f<first),default=0)
            self.current=self.checkpoints[start].copy();self.last=start

    def seek(self,frame):
        frame=max(0,min(len(self.tracks)-1,int(frame)))
        if frame<self.last:
            start=max((f for f in self.checkpoints if f<=frame),default=0)
            self.current=self.checkpoints[start].copy();self.last=start
        for f in range(self.last+1,frame+1):
            self.current=warp(self.current,self.tracks[f],self.fov[f],self.aspect[f],self.padding,self.sampling)
            if f%24==0:
                self.checkpoints[f]=self.current.copy()
                while len(self.checkpoints)>16:
                    key=next(k for k in self.checkpoints if k!=0);del self.checkpoints[key]
        self.last=frame
        return Image.fromarray(self.current)

"""Deforum Motion Studio 0.2 — standalone Windows motion schedule editor."""
import copy
import json
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

import numpy as np
from PIL import Image, ImageTk
from motion import (AXES,LABELS,LIMITS,PRESETS,load_config,parse_schedule,preset,
                    decode_audio,bass_envelope,pulse_positions,combine,export_config,resample)
from controller import Controller
from geometry import GeometryPreview

ROOT=Path(__file__).resolve().parent
COLORS=('#48c9b0','#59a5ff','#ffc857','#e77dff','#ff847c','#c1ce72')

class App:
    def __init__(self,root):
        self.root=root;root.title('Deforum Motion Studio 0.2');root.geometry('1250x900')
        root.minsize(1050,850)
        style=ttk.Style();style.theme_use('clam')
        self.config={};self.base=np.zeros((800,6));self.recorded=np.zeros_like(self.base)
        self.output=self.base.copy();self.fps=24.;self.frame=0;self.running=False;self.recording=False
        self.env=None;self.env_times=None;self.audio_path='';self.preview_image=None;self.tk_image=None
        self.undo_stack=[];self.controller=Controller();self.pad_smooth=np.zeros(6);self.last_buttons=0
        self.messages=queue.Queue();self.player=None;self.audio_busy=False;self.last_record=-1
        self.geometry=None;self.geometry_key=None
        self.vars={};self.limits=[];self.manual=[];self.enabled_axes=[];self.schedule_boxes=[]
        self.build()
        try: self.load_path(ROOT/'examples'/'working_baseline.txt',initial=True)
        except Exception as e: self.status.set('No baseline loaded: '+str(e))
        self.last_tick=time.monotonic();self.root.after(20,self.tick)
        self.root.after(150,self.rebuild)
        self.preview.bind('<Configure>',lambda e:self.draw_preview())
        self.graph.bind('<Configure>',lambda e:self.draw_graph())
        root.protocol('WM_DELETE_WINDOW',self.close)

    def variable(self,name,value):
        var=tk.BooleanVar(value=value) if type(value)==bool else tk.DoubleVar(value=value) if isinstance(value,(int,float)) else tk.StringVar(value=value)
        self.vars[name]=var;return var

    def number(self,parent,label,name,value,low,high,step=.01):
        row=ttk.Frame(parent);row.pack(fill='x',pady=3)
        ttk.Label(row,text=label,width=24).pack(side='left')
        var=self.variable(name,value)
        ttk.Spinbox(row,textvariable=var,from_=low,to=high,increment=step,width=10,command=self.rebuild).pack(side='right')
        return var

    def button(self,parent,text,command):
        b=ttk.Button(parent,text=text,command=lambda:self.guard(command));b.pack(side='left',padx=3,pady=3);return b

    def build(self):
        top=ttk.Frame(self.root,padding=8);top.pack(fill='x')
        ttk.Label(top,text='DEFORUM MOTION STUDIO',font=('Segoe UI',15,'bold')).pack(side='left',padx=8)
        for text,func in [('Load config',self.load_dialog),('Open project',self.open_project),('Save project',self.save_project),('Export to Deforum',self.export)]:self.button(top,text,func)
        self.info=tk.StringVar();ttk.Label(self.root,textvariable=self.info,padding=(15,0)).pack(anchor='w')
        self.status=tk.StringVar(value='Ready');ttk.Label(self.root,textvariable=self.status,padding=8,wraplength=1200).pack(side='bottom',fill='x')
        main=ttk.Panedwindow(self.root,orient='horizontal');main.pack(fill='both',expand=True,padx=10,pady=8)
        left=ttk.Frame(main,width=410);right=ttk.Frame(main);main.add(left,weight=0);main.add(right,weight=1)
        tabs=ttk.Notebook(left);tabs.pack(fill='both',expand=True)
        pages=[]
        for title in ('Presets','Bass pulse','Controller','Schedules'):
            page=ttk.Frame(tabs,padding=12);tabs.add(page,text=title);pages.append(page)
        p,a,c,s=pages
        ttk.Label(p,text='Choose a starting movement',font=('Segoe UI',12,'bold')).pack(anchor='w',pady=5)
        self.variable('preset','Wave');ttk.Combobox(p,textvariable=self.vars['preset'],values=PRESETS,state='readonly').pack(fill='x')
        self.number(p,'Strength (0–1)','strength',.5,0,1,.05)
        self.number(p,'Cycle length (seconds)','cycle',8,.2,120,.5)
        self.number(p,'Fade in/out (seconds)','fade',1,0,20,.1)
        self.number(p,'Start (seconds)','start',0,0,3600,.5)
        self.number(p,'End (0 = full video)','end',0,0,3600,.5)
        self.variable('preset_mode','Replace')
        ttk.Combobox(p,textvariable=self.vars['preset_mode'],values=('Replace','Add'),state='readonly').pack(fill='x',pady=4)
        row=ttk.Frame(p);row.pack(fill='x');self.button(row,'Apply preset',self.apply_preset);self.button(row,'Undo',self.undo)
        row=ttk.Frame(p);row.pack(fill='x');self.button(row,'Save custom preset',self.save_preset);self.button(row,'Load custom preset',self.load_preset)
        ttk.Separator(p).pack(fill='x',pady=12)
        ttk.Label(p,text='Continuous travel + timing',font=('Segoe UI',11,'bold')).pack(anchor='w')
        self.number(p,'Travel (+ forward / - back)','travel',0,-15,15,.05)
        self.number(p,'Video FPS','fps',24,1,120,1)
        self.number(p,'Number of frames','frames',800,2,30000,1)
        row=ttk.Frame(p);row.pack(fill='x');self.button(row,'Apply timing',self.apply_timing)
        ttk.Label(p,text='Timing changes preserve approximate speed per second.\nPresets change motion only. Render settings stay in the config.',wraplength=370).pack(anchor='w',pady=10)

        ttk.Label(a,text='Soundtrack -> bass -> distance pulse',font=('Segoe UI',12,'bold')).pack(anchor='w')
        row=ttk.Frame(a);row.pack(fill='x');self.button(row,'Load soundtrack',self.load_audio)
        self.audio_label=tk.StringVar(value='No soundtrack loaded')
        ttk.Label(a,textvariable=self.audio_label,wraplength=365).pack(anchor='w',pady=5)
        ttk.Checkbutton(a,text='Enable camera pulse',variable=self.variable('pulse_enabled',False),command=self.rebuild).pack(anchor='w',pady=5)
        self.variable('pulse_mode','Beat hits');ttk.Combobox(a,textvariable=self.vars['pulse_mode'],values=('Beat hits','Bass intensity'),state='readonly').pack(fill='x')
        for args in [('Pulse distance (relative units)','pulse_strength',2,0,30,.25),('Attack time (seconds)','attack',.08,.01,1,.01),('Return time (seconds)','release',.22,.02,2,.01),('Detection threshold (0–1)','threshold',.35,.05,1,.05),('Pulse timing offset (seconds)','offset',0,-5,5,.01),('Bass low cutoff (Hz)','low',35,10,500,5),('Bass high cutoff (Hz)','high',180,20,1000,5)]:self.number(a,*args)
        row=ttk.Frame(a);row.pack(fill='x');self.button(row,'Re-analyze bass',self.analyze_audio);self.button(row,'Update pulse',self.rebuild)
        ttk.Checkbutton(a,text='Attach soundtrack path to exported config',variable=self.variable('attach_audio',False)).pack(anchor='w',pady=8)
        ttk.Label(a,text='Pulse returns to the moving camera path. It does not\nstop continuous travel. + offset delays the pulse.\nDistance units are relative, not physical meters.\nAudio is analyzed up to the project duration.\nBeat detection follows bass peaks; adjust threshold/offset.',wraplength=370).pack(anchor='w',pady=8)

        ttk.Label(c,text='Xbox / mouse recording',font=('Segoe UI',12,'bold')).pack(anchor='w')
        self.pad_label=tk.StringVar();ttk.Label(c,textvariable=self.pad_label,wraplength=370).pack(anchor='w',pady=7)
        self.number(c,'Controller sensitivity','sensitivity',.5,.01,1,.05)
        self.number(c,'Stick dead zone','deadzone',.15,.05,.5,.01)
        self.number(c,'Smoothing (seconds)','smooth',.18,.01,1,.01)
        ttk.Label(c,text='Left stick: move | Right stick: turn/tilt\nTriggers: travel | Bumpers: roll\nA: record | B: stop | X: undo (when stopped)',wraplength=370).pack(anchor='w',pady=6)
        ttk.Label(c,text='Mouse sliders also record. Center = stopped.\nChecked axes replace the previous take in the recorded range.',wraplength=370).pack(anchor='w',pady=5)
        for i,label in enumerate(LABELS):
            row=ttk.Frame(c);row.pack(fill='x',pady=1)
            enabled=tk.BooleanVar(value=True);self.enabled_axes.append(enabled)
            ttk.Checkbutton(row,text=label,variable=enabled,width=21).pack(side='left')
            v=tk.DoubleVar(value=0);self.manual.append(v)
            ttk.Scale(row,variable=v,from_=-1,to=1).pack(side='left',fill='x',expand=True)
        row=ttk.Frame(c);row.pack(fill='x');self.button(row,'Center sliders',self.center);self.button(row,'Clear recording',self.clear_recording)
        ttk.Label(c,text='Combined per-frame speed limits',font=('Segoe UI',11,'bold')).pack(anchor='w',pady=(12,3))
        for i,label in enumerate(LABELS):
            row=ttk.Frame(c);row.pack(fill='x',pady=1);ttk.Label(row,text=label,width=24).pack(side='left')
            v=tk.DoubleVar(value=float(LIMITS[i]));self.limits.append(v)
            ttk.Spinbox(row,textvariable=v,from_=.01,to=30,increment=.05,width=9,command=self.rebuild).pack(side='right')
        row=ttk.Frame(c);row.pack(fill='x');self.button(row,'Apply limits',self.rebuild)

        ttk.Label(s,text='Native Deforum schedules (advanced)',font=('Segoe UI',11,'bold')).pack(anchor='w')
        ttk.Label(s,text='Edit keyframes or sin/cos expressions.\nThese values are movement per frame, not positions.',wraplength=370).pack(anchor='w',pady=6)
        for label in LABELS:
            ttk.Label(s,text=label).pack(anchor='w')
            box=tk.Text(s,height=3,width=43,wrap='word');box.pack(fill='x',pady=(0,7));self.schedule_boxes.append(box)
        row=ttk.Frame(s);row.pack(fill='x');self.button(row,'Apply schedules',self.apply_schedules)
        ttk.Label(s,text='Expressions support t, pi, sin, cos, abs, sqrt, exp, log.\nUnsupported expressions are rejected; never executed.',wraplength=370).pack(anchor='w',pady=5)

        row=ttk.Frame(right);row.pack(fill='x')
        ttk.Label(row,text='Deforum flat-depth geometry',font=('Segoe UI',12,'bold')).pack(side='left')
        self.button(row,'Load preview image',self.load_image)
        self.preview=tk.Canvas(right,bg='#101923',height=330,highlightthickness=0);self.preview.pack(fill='both',expand=True,pady=5)
        ttk.Label(right,text='Camera warp matched to your animation.py. AI regeneration and cadence blending are not simulated.').pack(anchor='w')
        row=ttk.Frame(right);row.pack(fill='x',pady=5)
        self.button(row,' ⏮',self.rewind);self.button(row,' Play',self.play);self.button(row,' Pause / Stop',self.stop);self.button(row,' Record',self.record);self.button(row,'Undo',self.undo)
        self.position_label=tk.StringVar();ttk.Label(row,textvariable=self.position_label).pack(side='right')
        self.timeline=tk.DoubleVar(value=0)
        self.seek=ttk.Scale(right,from_=0,to=799,variable=self.timeline,command=self.scrub);self.seek.pack(fill='x')
        self.graph=tk.Canvas(right,height=170,bg='#101923',highlightthickness=0);self.graph.pack(fill='x',pady=5)
        self.graph.bind('<Button-1>',self.graph_seek)
        ttk.Label(right,text='Speed curves normalized by each axis limit | click the graph to seek').pack(anchor='w')

    def guard(self,func):
        try:func()
        except Exception as e:messagebox.showerror('Motion Studio',str(e))
    def values(self):
        return {k:v.get() for k,v in self.vars.items()}
    def snapshot(self):
        self.undo_stack.append((self.base.copy(),self.recorded.copy(),self.fps,copy.deepcopy(self.config),self.values(),[v.get() for v in self.limits]))
        self.undo_stack=self.undo_stack[-15:]
    def undo(self):
        if self.running:self.stop()
        if not self.undo_stack:return
        self.base,self.recorded,self.fps,self.config,values,limits=self.undo_stack.pop()
        for k,v in values.items():self.vars[k].set(v)
        for var,value in zip(self.limits,limits):var.set(value)
        self.frame=min(self.frame,len(self.base)-1);self.refresh_schedules();self.rebuild()
    def load_dialog(self):
        path=filedialog.askopenfilename(filetypes=[('Deforum settings','*.txt *.json'),('All files','*')])
        if path:self.load_path(path)
    def load_path(self,path,initial=False):
        try:data,tracks,repairs=load_config(path)
        except ValueError as e:
            if 'comma' not in str(e):raise
            if not initial and not messagebox.askyesno('Repair schedule?',f'{e}\nInsert the missing separator in the imported motion copy? Original file stays unchanged.'):return
            data,tracks,repairs=load_config(path,repair=True)
        self.stop()
        if not initial:self.snapshot()
        self.config=data;self.base=tracks;self.recorded=np.zeros_like(tracks);self.fps=float(data['fps']);self.frame=0
        self.vars['fps'].set(self.fps);self.vars['frames'].set(len(tracks));self.vars['travel'].set(0);self.vars['pulse_enabled'].set(False)
        self.refresh_schedules();self.rebuild()
        self.info.set(f'{Path(path).name}  |  {data.get("W")} x {data.get("H")}  |  3D  |  cadence {data.get("diffusion_cadence")}  |  depth warping {data.get("use_depth_warping")}')
        if repairs:self.status.set('Imported baseline with missing comma repaired in '+', '.join(repairs)+'. Original file unchanged; rotations above 0.3 are limited in the composed output.')
    def refresh_schedules(self):
        for i,box in enumerate(self.schedule_boxes):
            box.delete('1.0','end')
            vals=self.base[:,i];indices=[0]
            if len(vals)>2:
                slope=np.diff(vals);indices.extend((np.where(np.abs(np.diff(slope))>1e-7)[0]+1).tolist())
            indices.append(len(vals)-1)
            box.insert('1.0',','.join(f'{j}:({vals[j]:.8f})' for j in sorted(set(indices))))
    def apply_schedules(self):
        tracks=np.column_stack([parse_schedule(b.get('1.0','end').strip(),len(self.base))[0] for b in self.schedule_boxes])
        self.stop();self.snapshot();self.base=tracks;self.rebuild()
    def apply_preset(self):
        v=self.values();n=len(self.base);a=round(v['start']*self.fps);b=n if v['end']==0 else round(v['end']*self.fps)
        if not 0<=a<b<=n:raise ValueError('Preset range must be inside the video')
        if not 0<=v['strength']<=1 or v['cycle']<=0 or v['fade']<0:raise ValueError('Invalid preset controls')
        part=preset(v['preset'],b-a,self.fps,v['strength'],v['cycle'],v['fade'])
        self.stop();self.snapshot()
        if v['preset_mode']=='Replace':self.base[a:b]=part;self.recorded[a:b]=0
        else:self.base[a:b]+=part
        self.refresh_schedules();self.rebuild()
    def apply_timing(self):
        fps=float(self.vars['fps'].get());n=int(self.vars['frames'].get())
        if not 1<=fps<=120 or not 2<=n<=30000:raise ValueError('Use 1–120 FPS and 2–30,000 frames')
        self.stop();self.snapshot();self.base=resample(self.base,self.fps,n,fps);self.recorded=resample(self.recorded,self.fps,n,fps)
        self.fps=fps;self.frame=0;self.refresh_schedules();self.rebuild()
        if self.audio_path:self.status.set('Timing changed. Re-analyze bass if the video duration was extended.')
    def rebuild(self):
        try:
            v=self.values();n=len(self.base);pulse=np.zeros(n)
            if v['pulse_enabled'] and self.env is not None:
                if v['attack']<=0 or v['release']<=0 or v['pulse_strength']<0:raise ValueError('Invalid pulse settings')
                pulse=pulse_positions(self.env,self.env_times,n,self.fps,v['pulse_strength'],v['attack'],v['release'],v['threshold'],v['offset'],v['pulse_mode'])
            self.output,scale,clipped=combine(self.base+self.recorded,v['travel'],pulse,[x.get() for x in self.limits])
            key=json.dumps({k:self.config.get(k) for k in ('W','H','use_depth_warping','enable_perspective_flip','shake_name','padding_mode','sampling_mode','fov_schedule','aspect_ratio_schedule','aspect_ratio_use_old_formula')},sort_keys=True)+str(n)
            if self.geometry is not None and key==self.geometry_key:self.geometry.update_tracks(self.output)
            else:
                self.geometry=None;self.geometry_key=key
                try:self.geometry=GeometryPreview(self.config,self.output.copy(),self.preview_image)
                except ValueError as e:self.geometry_error=str(e)
            self.seek.configure(to=n-1)
            self.status.set(f'{n/self.fps:.2f} seconds | Pulse retained {scale*100:.0f}%'+(' | Some base/recorded movement was limited' if clipped else '')+(' | Enable pulse after loading audio' if self.env is not None and not v['pulse_enabled'] else ''))
            self.draw_graph();self.draw_preview();return True
        except Exception as e:
            self.status.set('Check controls: '+str(e));return False
    def load_audio(self):
        path=filedialog.askopenfilename(filetypes=[('Audio','*.mp3 *.wav *.flac *.ogg *.m4a'),('All files','*')])
        if path:self.audio_path=path;self.analyze_audio()
    def analyze_audio(self):
        if not self.audio_path:raise ValueError('Load a soundtrack first')
        if self.audio_busy:return
        v=self.values();path=self.audio_path;duration=len(self.base)/self.fps
        if not 10<=v['low']<v['high']<4000:raise ValueError('Low bass cutoff must be below high cutoff')
        self.audio_busy=True;self.audio_label.set('Analyzing soundtrack...')
        def worker():
            try:
                samples,sr=decode_audio(path,duration)
                env,times=bass_envelope(samples,sr,v['low'],v['high'])
                self.messages.put(('audio',path,env,times))
            except Exception as e:self.messages.put(('error',str(e)))
        threading.Thread(target=worker,daemon=True).start()
    def load_image(self):
        path=filedialog.askopenfilename(filetypes=[('Images','*.png *.jpg *.jpeg *.webp')])
        if path:
            with Image.open(path) as im:self.preview_image=im.convert('RGB')
            self.geometry=None;self.rebuild()
    def start_audio(self):
        if not self.audio_path or not Path(self.audio_path).exists():return
        try:
            import pygame
            if not pygame.mixer.get_init():pygame.mixer.init()
            self.player=pygame.mixer.music;self.player.load(self.audio_path)
            # WAV seeking isn't supported reliably by pygame; preview from zero.
            if self.frame==0:self.player.play()
            elif Path(self.audio_path).suffix.lower() in ('.mp3','.ogg'):self.player.play(start=self.frame/self.fps)
            else:self.player=None;self.status.set('Visual preview from cursor; audio playback for this format starts only at frame 0.')
        except Exception as e:self.status.set('Audio playback unavailable: '+str(e))
    def play(self):
        if self.running:return
        if not self.rebuild():raise ValueError('Fix invalid controls before playback')
        if self.frame>=len(self.base)-1:self.frame=0
        self.running=True;self.clock=time.monotonic()-self.frame/self.fps;self.start_audio()
    def stop(self):
        was_recording=self.recording
        self.running=False;self.recording=False
        if self.player:
            try:self.player.stop()
            except Exception:pass
        self.player=None
        if was_recording:
            # Finish the take with a short deceleration rather than a hard cut.
            start=min(len(self.base)-1,self.frame)
            stop=min(len(self.base),start+max(2,round(.35*self.fps)))
            enabled=np.array([v.get() for v in self.enabled_axes])
            tail=np.linspace(1,0,stop-start)
            self.recorded[start:stop,enabled]=tail[:,None]*self.recorded[start,enabled][None,:]
            self.rebuild()
    def record(self):
        if self.recording:return
        if self.frame>=len(self.base)-1:self.frame=0
        self.snapshot();self.recording=True;self.last_record=self.frame-1;self.pad_smooth[:]=0;self.play()
    def center(self):
        for v in self.manual:v.set(0)
    def clear_recording(self):
        self.stop();self.snapshot();self.recorded[:]=0;self.rebuild()
    def scrub(self,value):
        if self.running:return
        self.frame=min(len(self.base)-1,max(0,int(float(value))));self.draw_preview()
    def graph_seek(self,event):
        self.stop();self.frame=int(np.clip((event.x-10)/max(1,self.graph.winfo_width()-20),0,1)*(len(self.base)-1));self.timeline.set(self.frame);self.draw_preview()
    def tick(self):
        now=time.monotonic();dt=min(.1,now-self.last_tick);self.last_tick=now
        try:
            while True:
                result=self.messages.get_nowait();self.audio_busy=False
                if result[0]=='audio':
                    _,path,self.env,self.env_times=result;self.audio_label.set(f'{Path(path).name} | analyzed {self.env_times[-1]:.1f}s');self.vars['pulse_enabled'].set(True);self.rebuild()
                else:self.audio_label.set('Analysis failed');messagebox.showerror('Audio',result[1])
        except queue.Empty:pass
        try:
            pad,buttons,label=self.controller.read(float(self.vars['deadzone'].get()));self.pad_label.set(label)
            pressed=buttons&~self.last_buttons;self.last_buttons=buttons
            if pressed&0x1000:self.guard(self.record)
            if pressed&0x2000:self.stop()
            if pressed&0x4000 and not self.running:self.undo()
            target=np.clip(pad+np.array([v.get() for v in self.manual]),-1,1)*float(self.vars['sensitivity'].get())*np.array([v.get() for v in self.limits])
            alpha=1-math.exp(-dt/max(.01,float(self.vars['smooth'].get())))
            self.pad_smooth+=(target-self.pad_smooth)*alpha
            if self.running:
                next_frame=min(len(self.base)-1,int((now-self.clock)*self.fps))
                if self.recording:
                    enabled=np.array([v.get() for v in self.enabled_axes])
                    for f in range(self.last_record+1,next_frame+1):
                        self.recorded[f,enabled]=self.pad_smooth[enabled]
                    self.last_record=next_frame
                self.frame=next_frame;self.timeline.set(self.frame)
                if self.recording:self.rebuild()
                else:self.draw_preview()
                if self.frame==len(self.base)-1:self.stop();self.status.set('Playback / recording finished. Review the curves and export when ready.')
        except Exception as e:self.status.set(str(e));self.stop()
        self.root.after(20,self.tick)

    def rewind(self):
        self.stop()
        self.frame=0;self.timeline.set(0);self.draw_preview()

    def draw_preview(self):
        c=self.preview;c.delete('all');w=max(100,c.winfo_width());h=max(100,c.winfo_height())
        f=min(self.frame,len(self.output)-1)
        if self.geometry is not None:
            try:
                im, done = self.geometry.seek(f, max_steps=4 if not self.running else 1)
                im.thumbnail((max(16,w-30),max(16,h-50)),Image.Resampling.LANCZOS)
                self.tk_image=ImageTk.PhotoImage(im);c.create_image(w/2,h/2+10,image=self.tk_image)
                title=f'Native {self.geometry.width}x{self.geometry.height} | FOV {self.geometry.fov[self.geometry.last]:.1f} | aspect {self.geometry.aspect[self.geometry.last]:.3f} | frame-by-frame warp'
                if not done:
                    title += f' (computing {self.geometry.last}/{f}...)'
                    self.root.after(10, self.draw_preview)
            except ValueError as e:title=str(e)
        else:title=getattr(self,'geometry_error','Loading geometry...')
        c.create_text(12,12,anchor='nw',fill='white',width=max(80,w-24),text=title)
        self.position_label.set(f'Frame {f} / {len(self.output)-1}   |   {f/self.fps:.2f}s'+('   RECORDING' if self.recording else ''))
        self.draw_graph(cursor_only=True)
    def draw_graph(self,cursor_only=False):
        c=self.graph;w=max(100,c.winfo_width());h=max(100,c.winfo_height())
        if not cursor_only:
            c.delete('all');c.create_line(10,h/2,w-10,h/2,fill='#556677')
            step=max(1,len(self.output)//max(10,w))
            limits=np.array([x.get() for x in self.limits])
            for i in range(6):
                ids=np.unique(np.r_[np.arange(0,len(self.output),step),len(self.output)-1])
                coords=[]
                for f in ids:coords.extend((10+f/(len(self.output)-1)*(w-20),h/2-self.output[f,i]/limits[i]*(h/2-24)))
                c.create_line(*coords,fill=COLORS[i],width=1.5)
                c.create_text(12+i*(w-24)/6,8,anchor='nw',text=('X','Y','Travel','Tilt','Turn','Roll')[i],fill=COLORS[i])
        c.delete('cursor');x=10+self.frame/(len(self.output)-1)*(w-20);c.create_line(x,23,x,h,fill='white',tags='cursor')

    def save_project(self):
        path=filedialog.asksaveasfilename(defaultextension='.dms.json',filetypes=[('Motion Studio project','*.dms.json')])
        if not path:return
        data={'format':'deforum-motion-studio','version':1,'config':self.config,'base':self.base.tolist(),'recorded':self.recorded.tolist(),'fps':self.fps,'controls':self.values(),'limits':[v.get() for v in self.limits],'audio_path':self.audio_path,'bass':None if self.env is None else self.env.tolist(),'bass_times':None if self.env_times is None else self.env_times.tolist()}
        Path(path).write_text(json.dumps(data,indent=2),encoding='utf-8');self.status.set('Project saved, including bass analysis. Back up the soundtrack separately.')
    def open_project(self):
        path=filedialog.askopenfilename(filetypes=[('Motion Studio project','*.dms.json')])
        if not path:return
        d=json.loads(Path(path).read_text(encoding='utf-8'))
        if d.get('format')!='deforum-motion-studio':raise ValueError('Not a Motion Studio project')
        base=np.asarray(d['base'],float);recorded=np.asarray(d['recorded'],float)
        if base.ndim!=2 or base.shape[1]!=6 or base.shape!=recorded.shape or not 2<=len(base)<=30000 or not np.all(np.isfinite(base+recorded)):raise ValueError('Invalid project motion')
        fps=float(d['fps'])
        if not 1<=fps<=120:raise ValueError('Invalid project FPS')
        self.stop();self.snapshot();self.config=d['config'];self.base=base;self.recorded=recorded;self.fps=fps;self.frame=0
        for k,v in d['controls'].items():
            if k in self.vars:self.vars[k].set(v)
        for var,value in zip(self.limits,d['limits']):var.set(value)
        self.audio_path=d.get('audio_path','');self.env=np.array(d['bass']) if d.get('bass') is not None else None;self.env_times=np.array(d['bass_times']) if d.get('bass_times') is not None else None
        self.audio_label.set(Path(self.audio_path).name or 'No soundtrack');self.refresh_schedules();self.rebuild();self.info.set('Project: '+Path(path).name)
    def save_preset(self):
        path=filedialog.asksaveasfilename(defaultextension='.preset.json')
        if path:Path(path).write_text(json.dumps({'format':'dms-preset','fps':self.fps,'tracks':(self.base+self.recorded).tolist()},indent=2),encoding='utf-8')
    def load_preset(self):
        path=filedialog.askopenfilename(filetypes=[('Motion preset','*.preset.json')])
        if not path:return
        d=json.loads(Path(path).read_text(encoding='utf-8'));tracks=np.array(d['tracks'],float);fps=float(d['fps'])
        if d.get('format')!='dms-preset' or tracks.ndim!=2 or tracks.shape[1]!=6 or len(tracks)<2 or not np.all(np.isfinite(tracks)) or not 1<=fps<=120:raise ValueError('Invalid custom preset')
        self.stop();self.snapshot();self.base=resample(tracks,fps,len(self.base),self.fps);self.recorded[:]=0;self.refresh_schedules();self.rebuild()
    def export(self):
        self.stop()
        if not self.rebuild():raise ValueError('Fix invalid controls before export')
        if self.config.get('parseq_manifest'):
            raise ValueError('This config has a Parseq manifest that may override motion. Clear it in Deforum and save a baseline first.')
        path=filedialog.asksaveasfilename(defaultextension='.txt',initialfile='deforum_motion_settings.txt',filetypes=[('Deforum settings','*.txt')])
        if not path:return
        config=copy.deepcopy(self.config)
        if self.vars['attach_audio'].get() and self.audio_path:
            config['add_soundtrack']='File';config['soundtrack_path']=str(Path(self.audio_path).resolve())
        export_config(config,self.output,self.fps,path)
        self.status.set('Exported. In Forge: Deforum -> Load All Settings -> select this file. Test a short render before a full video.')
    def close(self):
        self.stop();self.root.destroy()

if __name__=='__main__':
    root=tk.Tk();app=App(root);root.mainloop()

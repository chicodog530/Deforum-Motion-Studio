"""Deforum Motion Studio 0.3 — standalone Windows motion schedule editor."""
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
from motion import (AXES,LABELS,LIMITS,PRESETS,CHANNEL_MODES,DEFAULT_CHANNELS,CHANNEL_SR,
                    load_config,parse_schedule,preset,keyframe_indices,
                    decode_audio,bass_envelope,spectrum_db,freq_class,pulse_positions,channel_pulse,validate_channel,
                    combine,combine_multi,export_config,resample)
from controller import (Controller,STICK_INPUTS,BUTTON_INPUTS,BUTTON_BITS,ACTIONS,
                        default_pad_map,validate_pad_map,pad_axis_target,button_action)
from video_review import VideoReview
from geometry import GeometryPreview

ROOT=Path(__file__).resolve().parent
COLORS=('#48c9b0','#59a5ff','#ffc857','#e77dff','#ff847c','#c1ce72')
CHANNEL_COLORS=('#59a5ff','#ffc857','#e77dff','#ff847c','#c1ce72','#48c9b0','#9df2d5','#f2a49d')
SETTINGS_FILE=ROOT/'settings.json'

class App:
    def __init__(self,root):
        self.root=root;root.title('Deforum Motion Studio 0.3');root.geometry('1250x900')
        root.minsize(1050,850)
        # Persistent UI settings (theme, gamepad mapping); failures fall back to defaults.
        dark=True;pad_map=None
        try:
            _settings=json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
            dark=bool(_settings.get('dark_mode',True));pad_map=_settings.get('pad_map')
        except Exception: pass
        self.dark=tk.BooleanVar(value=dark)
        self.pad_map=validate_pad_map(pad_map,LIMITS)
        style=ttk.Style();style.theme_use('clam')
        self.config={};self.base=np.zeros((800,6));self.recorded=np.zeros_like(self.base)
        self.output=self.base.copy();self.fps=24.;self.frame=0;self.running=False;self.recording=False
        self.audio_path='';self.preview_image=None;self.tk_image=None
        self.video_path='';self.video=None;self.loop_a=None;self.loop_b=None
        self.preview_mode=tk.StringVar(value='wireframe');self.loop_on=tk.BooleanVar(value=False)
        self._video_key=None;self._video_ok=False;self._preview_mode_shown=None
        self.tk_video=None
        self.channels=[];self.reset_channels();self.spectrum=None;self.spec_cursor_f=None
        self._audio_cache=None;self._reanalyze_after=None
        self.undo_stack=[];self.controller=Controller();self.pad_smooth=np.zeros(6);self.last_buttons=0
        self.messages=queue.Queue();self.player=None;self.audio_busy=False;self.last_record=-1
        self.geometry=None;self.geometry_key=None
        self._rebuild_pending=False;self._preview_queued=False
        self._loading_channel=False;self.ch_vars={}
        self.vars={};self.limits=[];self.manual=[];self.enabled_axes=[];self.schedule_boxes=[]
        self.build()
        self.apply_theme()
        try: self.load_path(ROOT/'examples'/'working_baseline.txt',initial=True)
        except Exception as e: self.status.set('No baseline loaded: '+str(e))
        self.last_tick=time.monotonic();self.root.after(20,self.tick)
        self.root.after(150,self.rebuild)
        self.preview.bind('<Configure>',lambda e:self.draw_preview())
        self.preview_video.bind('<Configure>',lambda e:self.draw_preview())
        self.graph.bind('<Configure>',lambda e:self.draw_graph())
        root.protocol('WM_DELETE_WINDOW',self.close)

    def reset_channels(self):
        self.channels=[]
        for defaults in DEFAULT_CHANNELS:
            ch=validate_channel(defaults);ch['env']=None;ch['times']=None;ch['peak_hz']=None
            self.channels.append(ch)

    def request_rebuild(self):
        """Debounced rebuild: coalesces rapid control changes to one rebuild
        per UI tick instead of a synchronous rebuild per spinbox nudge."""
        self._rebuild_pending=True

    def variable(self,name,value):
        var=tk.BooleanVar(value=value) if type(value)==bool else tk.DoubleVar(value=value) if isinstance(value,(int,float)) else tk.StringVar(value=value)
        self.vars[name]=var;return var

    def number(self,parent,label,name,value,low,high,step=.01):
        row=ttk.Frame(parent);row.pack(fill='x',pady=3)
        ttk.Label(row,text=label,width=24).pack(side='left')
        var=self.variable(name,value)
        ttk.Spinbox(row,textvariable=var,from_=low,to=high,increment=step,width=10,command=self.request_rebuild).pack(side='right')
        return var

    def button(self,parent,text,command):
        b=ttk.Button(parent,text=text,command=lambda:self.guard(command));b.pack(side='left',padx=3,pady=3);return b

    # ---- Sound channels tab ----
    def build_channels_tab(self,a):
        ttk.Label(a,text='Soundtrack -> detection channels -> motion effects',font=('Segoe UI',12,'bold')).pack(anchor='w')
        row=ttk.Frame(a);row.pack(fill='x')
        self.button(row,'Load soundtrack',self.load_audio)
        self.audio_label=tk.StringVar(value='No soundtrack loaded')
        ttk.Label(a,textvariable=self.audio_label,wraplength=365).pack(anchor='w',pady=5)
        mid=ttk.Frame(a);mid.pack(fill='both',expand=True)
        left=ttk.Frame(mid);left.pack(side='left',fill='y',padx=(0,8))
        self.channel_list=tk.Listbox(left,height=7,width=20,exportselection=False)
        self.channel_list.pack(fill='y',expand=True)
        self.channel_list.bind('<<ListboxSelect>>',lambda e:self._select_channel())
        brow=ttk.Frame(left);brow.pack(fill='x',pady=4)
        ttk.Button(brow,text='Add',width=5,command=lambda:self.guard(self._add_channel)).pack(side='left',padx=1)
        ttk.Button(brow,text='Dupe',width=5,command=lambda:self.guard(self._duplicate_channel)).pack(side='left',padx=1)
        ttk.Button(brow,text='Del',width=4,command=lambda:self.guard(self._remove_channel)).pack(side='left',padx=1)
        ed=ttk.Frame(mid);ed.pack(side='left',fill='both',expand=True)
        self.ch_vars={};self._loading_channel=False
        r=ttk.Frame(ed);r.pack(fill='x',pady=2)
        ttk.Label(r,text='Name',width=15).pack(side='left')
        self._ch_var('name','');ttk.Entry(r,textvariable=self.ch_vars['name'],width=15).pack(side='right')
        self._ch_var('enabled',True);ttk.Checkbutton(ed,text='Channel enabled',variable=self.ch_vars['enabled']).pack(anchor='w')
        r=ttk.Frame(ed);r.pack(fill='x',pady=2)
        ttk.Label(r,text='Drives axis',width=15).pack(side='left')
        self._ch_var('axis',LABELS[2]);ttk.Combobox(r,textvariable=self.ch_vars['axis'],values=LABELS,state='readonly',width=13).pack(side='right')
        r=ttk.Frame(ed);r.pack(fill='x',pady=2)
        ttk.Label(r,text='Detection',width=15).pack(side='left')
        self._ch_var('mode',CHANNEL_MODES[0]);ttk.Combobox(r,textvariable=self.ch_vars['mode'],values=CHANNEL_MODES,state='readonly',width=13).pack(side='right')
        for label,key,low,high,step in [
                ('Low cutoff (Hz)','low',10,12000,50),('High cutoff (Hz)','high',20,11024,50),
                ('Strength','strength',0,30,.25),('Attack (s)','attack',.01,2,.01),
                ('Return (s)','release',.02,2,.01),('Threshold (0-1)','threshold',.05,1,.05),
                ('Timing offset (s)','offset',-5,5,.01),('Min gap (s)','min_gap',.02,5,.01),
                ('Start (s)','start',0,3600,.5),('End (s, 0=full)','end',0,3600,.5)]:
            r=ttk.Frame(ed);r.pack(fill='x',pady=1)
            ttk.Label(r,text=label,width=15).pack(side='left')
            self._ch_var(key,0.0)
            ttk.Spinbox(r,textvariable=self.ch_vars[key],from_=low,to=high,increment=step,width=9).pack(side='right')
        row=ttk.Frame(a);row.pack(fill='x',pady=6)
        self.button(row,'Sample soundtrack',self.sample_soundtrack)
        self.button(row,'Re-analyze channels',self.analyze_audio)
        self.button(row,'Update now',self.rebuild)
        ttk.Checkbutton(a,text='Attach soundtrack path to exported config',variable=self.variable('attach_audio',False)).pack(anchor='w',pady=4)
        ttk.Label(a,text='Each channel watches one frequency band and drives one axis.\nChannels may overlap; overlapping pulses on one axis add up.\nStart/end pick where the effect applies (end 0 = full video).',wraplength=370).pack(anchor='w',pady=4)
        self._refresh_channel_list()
        if self.channels:self._select_only(0);self._select_channel()

    def build_gamepad_tab(self,g):
        ttk.Label(g,text='Gamepad mapping & test',font=('Segoe UI',12,'bold')).pack(anchor='w')
        ttk.Label(g,text='Assign each stick, trigger and button. Wiggle the controller: live values appear on the right. Max = movement per second at full deflection, set per control.',wraplength=380).pack(anchor='w',pady=4)
        self._pad_loading=True
        self.pad_vars={};self.pad_test_vars={};self.pad_btn_test_vars={}
        axis_names=['Unassigned']+list(LABELS)
        for inp,label in STICK_INPUTS:
            row=ttk.Frame(g);row.pack(fill='x',pady=1)
            ttk.Label(row,text=label,width=13).pack(side='left')
            tvar=tk.StringVar()
            ttk.Combobox(row,textvariable=tvar,values=axis_names,state='readonly',width=14).pack(side='left')
            tvar.trace_add('write',lambda *a:self._pad_map_edited())
            ttk.Label(row,text='Max',padding=(6,0)).pack(side='left')
            mvar=tk.DoubleVar()
            ttk.Spinbox(row,textvariable=mvar,from_=0,to=30,increment=.05,width=6).pack(side='left')
            mvar.trace_add('write',lambda *a:self._pad_map_edited())
            ivar=tk.BooleanVar();ttk.Checkbutton(row,text='Inv',variable=ivar,width=5).pack(side='left')
            ivar.trace_add('write',lambda *a:self._pad_map_edited())
            live=tk.StringVar(value='+0.00')
            ttk.Label(row,textvariable=live,width=7).pack(side='right')
            self.pad_vars[inp]={'target':tvar,'max':mvar,'invert':ivar}
            self.pad_test_vars[inp]=live
        ttk.Separator(g).pack(fill='x',pady=8)
        ttk.Label(g,text='Buttons (action, or hold to drive an axis)',font=('Segoe UI',11,'bold')).pack(anchor='w')
        btn_targets=['Unassigned']+[lbl for _,lbl in ACTIONS]+list(LABELS)
        for b,label in BUTTON_INPUTS:
            row=ttk.Frame(g);row.pack(fill='x',pady=1)
            ttk.Label(row,text=label,width=13).pack(side='left')
            avar=tk.StringVar()
            ttk.Combobox(row,textvariable=avar,values=btn_targets,state='readonly',width=14).pack(side='left')
            maxframe=ttk.Frame(row);maxframe.pack(side='left')
            ttk.Label(maxframe,text='Max',padding=(6,0)).pack(side='left')
            mvar=tk.DoubleVar()
            sp=ttk.Spinbox(maxframe,textvariable=mvar,from_=0,to=30,increment=.05,width=6)
            sp.pack(side='left')
            ivar=tk.BooleanVar();icb=ttk.Checkbutton(maxframe,text='Inv',variable=ivar,width=5)
            icb.pack(side='left')
            avar.trace_add('write',lambda *a,b=b:self._pad_btn_target_changed(b))
            mvar.trace_add('write',lambda *a:self._pad_map_edited())
            ivar.trace_add('write',lambda *a:self._pad_map_edited())
            live=tk.StringVar(value='○')
            ttk.Label(row,textvariable=live,width=7).pack(side='right')
            self.pad_vars['btn_'+b]={'target':avar,'max':mvar,'invert':ivar,
                                     'maxframe':maxframe,'spin':sp,'invbtn':icb}
            self.pad_btn_test_vars[b]=live
        ttk.Button(g,text='Reset to defaults',command=lambda:self.guard(self._reset_pad_map)).pack(anchor='w',pady=8)
        self._pad_loading=False
        self._sync_pad_ui()
    def _pad_btn_target_changed(self,b):
        # Max/Invert only apply when a button drives an axis; grey them otherwise.
        is_axis=self.pad_vars['btn_'+b]['target'].get() in LABELS
        st='!disabled' if is_axis else 'disabled'
        self.pad_vars['btn_'+b]['spin'].state([st] if is_axis else ['disabled'])
        self.pad_vars['btn_'+b]['invbtn'].state([st] if is_axis else ['disabled'])
        self._pad_map_edited()
    def _sync_pad_ui(self):
        self._pad_loading=True
        try:
            for inp,_ in STICK_INPUTS:
                cfg=self.pad_map.get(inp,{})
                tgt=cfg.get('target','') if isinstance(cfg,dict) else ''
                name=LABELS[int(tgt[5:])] if tgt.startswith('axis:') and tgt[5:].isdigit() else 'Unassigned'
                self.pad_vars[inp]['target'].set(name)
                self.pad_vars[inp]['max'].set(float(cfg.get('max',0)) if isinstance(cfg,dict) else 0.0)
                self.pad_vars[inp]['invert'].set(bool(cfg.get('invert')) if isinstance(cfg,dict) else False)
            acts={f'action:{a}':lbl for a,lbl in ACTIONS}
            axes={f'axis:{i}':lbl for i,lbl in enumerate(LABELS)}
            for b,_ in BUTTON_INPUTS:
                cfg=self.pad_map.get('btn_'+b,{})
                tgt=cfg.get('target','') if isinstance(cfg,dict) else ''
                v=self.pad_vars['btn_'+b]
                v['target'].set(acts.get(tgt,axes.get(tgt,'Unassigned')))
                v['max'].set(float(cfg.get('max',0)) if isinstance(cfg,dict) else 0.0)
                v['invert'].set(bool(cfg.get('invert')) if isinstance(cfg,dict) else False)
                is_axis=v['target'].get() in LABELS
                v['spin'].state(['!disabled'] if is_axis else ['disabled'])
                v['invbtn'].state(['!disabled'] if is_axis else ['disabled'])
        finally:self._pad_loading=False
    def _pad_map_edited(self):
        if getattr(self,'_pad_loading',False):return
        new={}
        for inp,_ in STICK_INPUTS:
            v=self.pad_vars[inp];t=v['target'].get()
            try:maxv=max(0.0,min(30.0,float(v['max'].get())))
            except (tk.TclError,TypeError,ValueError):return
            new[inp]={'target':f'axis:{LABELS.index(t)}' if t in LABELS else '',
                      'max':maxv,'invert':bool(v['invert'].get())}
        acts={lbl:f'action:{a}' for a,lbl in ACTIONS}
        axes={lbl:f'axis:{i}' for i,lbl in enumerate(LABELS)}
        for b,_ in BUTTON_INPUTS:
            v=self.pad_vars['btn_'+b];t=v['target'].get()
            try:maxv=max(0.0,min(30.0,float(v['max'].get())))
            except (tk.TclError,TypeError,ValueError):return
            new['btn_'+b]={'target':acts.get(t,axes.get(t,'')),
                           'max':maxv,'invert':bool(v['invert'].get())}
        self.pad_map=validate_pad_map(new,LIMITS)
        self._save_settings({'pad_map':self.pad_map})
    def _reset_pad_map(self):
        self.pad_map=default_pad_map(LIMITS)
        self._save_settings({'pad_map':self.pad_map})
        self._sync_pad_ui()
        self.status.set('Gamepad mapping reset to defaults.')
    def _ch_var(self,key,default):
        if isinstance(default,bool):var=tk.BooleanVar(value=default)
        elif isinstance(default,(int,float)):var=tk.DoubleVar(value=default)
        else:var=tk.StringVar(value=default)
        var.trace_add('write',lambda *a:self._channel_edited())
        self.ch_vars[key]=var;return var

    def _selected_channel(self):
        sel=self.channel_list.curselection() if hasattr(self,'channel_list') else ()
        return sel[-1] if sel and sel[-1]<len(self.channels) else None

    def _select_only(self,i):
        self.channel_list.select_clear(0,'end')
        self.channel_list.select_set(i)

    def _select_channel(self):
        i=self._selected_channel();self._loading_channel=True
        try:
            if i is None:return
            ch=self.channels[i]
            self.ch_vars['name'].set(ch['name']);self.ch_vars['enabled'].set(ch['enabled'])
            self.ch_vars['axis'].set(LABELS[ch['axis']]);self.ch_vars['mode'].set(ch['mode'])
            for k in ('low','high','strength','attack','release','threshold','offset','min_gap','start','end'):
                self.ch_vars[k].set(ch[k])
        finally:self._loading_channel=False

    def _channel_edited(self):
        if self._loading_channel:return
        i=self._selected_channel()
        if i is None:return
        ch=self.channels[i]
        old_band=ch.get('_band',(ch.get('low'),ch.get('high')))
        try:
            name=str(self.ch_vars['name'].get())[:32] or 'Channel'
            axis=LABELS.index(self.ch_vars['axis'].get())
            mode=self.ch_vars['mode'].get()
            vals={k:float(self.ch_vars[k].get()) for k in ('low','high','strength','attack','release','threshold','offset','min_gap','start','end')}
            if not vals['low']<vals['high'] or mode not in CHANNEL_MODES:return
            if vals['end']!=0 and not vals['end']>vals['start']:return
        except Exception:return
        ch.update(name=name,axis=axis,mode=mode,enabled=bool(self.ch_vars['enabled'].get()),**vals)
        self._refresh_channel_list_name(i)
        if (ch['low'],ch['high'])!=tuple(old_band):
            self._band_changed(i)
        self.request_rebuild()

    def _cancel_pending_reanalyze(self):
        if self._reanalyze_after:
            try:self.root.after_cancel(self._reanalyze_after)
            except Exception:pass
            self._reanalyze_after=None

    def _band_changed(self,i):
        """A channel's frequency band changed: its envelope is stale, so drop
        it and recompute from the cached audio (debounced while typing)."""
        self._cancel_pending_reanalyze()
        if not 0<=i<len(self.channels):return
        ch=self.channels[i]
        ch['env']=None;ch['times']=None;ch['peak_hz']=None
        if self._audio_cache and self._audio_cache[0]==self.audio_path and self.audio_path:
            self.status.set(f"Band changed — re-analyzing '{ch['name']}'...")
            self._reanalyze_after=self.root.after(800,lambda:self._reanalyze_channel(i))
        else:
            self.status.set('Band changed — load audio and click Re-analyze channels')
        self.request_rebuild()

    def _reanalyze_channel(self,i):
        self._reanalyze_after=None
        if not 0<=i<len(self.channels):return
        if not (self._audio_cache and self._audio_cache[0]==self.audio_path):return
        ch=self.channels[i];lo,hi=ch['low'],ch['high']
        if not 10<=lo<hi<CHANNEL_SR/2:return
        path,samples,sr=self._audio_cache
        self.status.set(f"Re-analyzing '{ch['name']}' ({lo:.0f}–{hi:.0f} Hz)...")
        def worker():
            try:
                env,times=bass_envelope(samples,sr,lo,hi)
                peak=None
                if self.spectrum is not None:
                    sf,sdb=self.spectrum
                    m=(sf>=lo)&(sf<=hi)
                    if np.any(m):peak=float(sf[m][np.argmax(sdb[m])])
                self.messages.put(('channel',i,env,times,peak,lo,hi))
            except Exception as e:self.messages.put(('error',str(e)))
        threading.Thread(target=worker,daemon=True).start()

    def _refresh_channel_list(self):
        self.channel_list.delete(0,'end')
        for ch in self.channels:
            self.channel_list.insert('end',('\u2713 ' if ch['enabled'] else '\u2717 ')+ch['name'])

    def _refresh_channel_list_name(self,i):
        ch=self.channels[i]
        self.channel_list.delete(i)
        self.channel_list.insert(i,('\u2713 ' if ch['enabled'] else '\u2717 ')+ch['name'])
        self._select_only(i)

    def _add_channel(self):
        self.snapshot()
        ch=validate_channel(dict(DEFAULT_CHANNELS[0],name=f'Channel {len(self.channels)+1}'))
        ch['env']=None;ch['times']=None;ch['peak_hz']=None
        self.channels.append(ch);self._refresh_channel_list()
        self._select_only(len(self.channels)-1)
        self._select_channel();self.request_rebuild()

    def _duplicate_channel(self):
        i=self._selected_channel()
        if i is None:return
        self.snapshot()
        ch={k:(v.copy() if isinstance(v,np.ndarray) else v) for k,v in self.channels[i].items()}
        ch['name']=(ch['name']+' copy')[:32]
        self.channels.append(ch);self._refresh_channel_list()
        self._select_only(len(self.channels)-1)
        self._select_channel();self.request_rebuild()

    def _remove_channel(self):
        if len(self.channels)<=1:raise ValueError('Keep at least one channel')
        i=self._selected_channel()
        if i is None:return
        self._cancel_pending_reanalyze()
        self.snapshot();del self.channels[i];self._refresh_channel_list()
        self._select_only(min(i,len(self.channels)-1));self._select_channel();self.rebuild()

    def build(self):
        top=ttk.Frame(self.root,padding=8);top.pack(fill='x')
        ttk.Label(top,text='DEFORUM MOTION STUDIO',font=('Segoe UI',15,'bold')).pack(side='left',padx=8)
        for text,func in [('Load config',self.load_dialog),('Open project',self.open_project),('Save project',self.save_project),('Export to Deforum',self.export)]:self.button(top,text,func)
        ttk.Checkbutton(top,text='Dark mode',variable=self.dark,command=self.apply_theme).pack(side='right',padx=8)
        self.info=tk.StringVar();ttk.Label(self.root,textvariable=self.info,padding=(15,0)).pack(anchor='w')
        self.status=tk.StringVar(value='Ready');ttk.Label(self.root,textvariable=self.status,padding=8,wraplength=1200).pack(side='bottom',fill='x')
        main=ttk.Panedwindow(self.root,orient='horizontal');main.pack(fill='both',expand=True,padx=10,pady=8)
        left=ttk.Frame(main,width=410);right=ttk.Frame(main);main.add(left,weight=0);main.add(right,weight=1)
        tabs=ttk.Notebook(left);tabs.pack(fill='both',expand=True)
        pages=[]
        for title in ('Presets','Sound channels','Controller','Gamepad','Schedules'):
            page=ttk.Frame(tabs,padding=12);tabs.add(page,text=title);pages.append(page)
        p,a,c,g,s=pages
        ttk.Label(p,text='Choose a starting movement',font=('Segoe UI',12,'bold')).pack(anchor='w',pady=5)
        self.variable('preset','Wave');ttk.Combobox(p,textvariable=self.vars['preset'],values=PRESETS,state='readonly').pack(fill='x')
        self.number(p,'Strength (0–1)','strength',.5,0,1,.05)
        self.number(p,'Cycle length (seconds)','cycle',8,.2,120,.5)
        self.number(p,'Fade in/out (seconds)','fade',1,0,20,.1)
        self.number(p,'Start (seconds)','start',0,0,3600,.5)
        self.number(p,'End (0 = full video)','end',0,0,3600,.5)
        self.variable('preset_mode','Replace')
        ttk.Combobox(p,textvariable=self.vars['preset_mode'],values=('Replace','Add'),state='readonly').pack(fill='x',pady=4)
        self.variable('auto_music',True)
        ttk.Checkbutton(p,text='Auto-add Bass pulse + Mid ripple channels',variable=self.vars['auto_music']).pack(anchor='w',pady=4)
        ttk.Label(p,text='Applies to the Reveal drift / Fly-through presets.',font=('Segoe UI',8)).pack(anchor='w')
        row=ttk.Frame(p);row.pack(fill='x');self.button(row,'Apply preset',self.apply_preset);self.button(row,'Undo',self.undo)
        row=ttk.Frame(p);row.pack(fill='x');self.button(row,'Save custom preset',self.save_preset);self.button(row,'Load custom preset',self.load_preset)
        ttk.Separator(p).pack(fill='x',pady=12)
        ttk.Label(p,text='Continuous travel + timing',font=('Segoe UI',11,'bold')).pack(anchor='w')
        self.number(p,'Travel (+ forward / - back)','travel',0,-15,15,.05)
        self.number(p,'Video FPS','fps',24,1,120,1)
        self.number(p,'Number of frames','frames',800,2,30000,1)
        row=ttk.Frame(p);row.pack(fill='x');self.button(row,'Apply timing',self.apply_timing)
        ttk.Label(p,text='Timing changes preserve approximate speed per second.\nPresets change motion only. Render settings stay in the config.',wraplength=370).pack(anchor='w',pady=10)

        self.build_channels_tab(a)

        ttk.Label(c,text='Xbox / mouse recording',font=('Segoe UI',12,'bold')).pack(anchor='w')
        self.pad_label=tk.StringVar();ttk.Label(c,textvariable=self.pad_label,wraplength=370).pack(anchor='w',pady=7)
        self.number(c,'Mouse sensitivity','sensitivity',.5,.01,1,.05)
        self.number(c,'Stick dead zone','deadzone',.15,.05,.5,.01)
        self.number(c,'Stick response (1=linear)','expo',1.,1.,2.5,.1)
        self.number(c,'Smoothing (seconds)','smooth',.18,.01,1,.01)
        ttk.Label(c,text='Assign sticks, triggers and buttons on the Gamepad tab.\nEach control has its own max; mouse sliders use the sensitivity above.',wraplength=370).pack(anchor='w',pady=6)
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
            ttk.Spinbox(row,textvariable=v,from_=.01,to=30,increment=.05,width=9,command=self.request_rebuild).pack(side='right')
        row=ttk.Frame(c);row.pack(fill='x');self.button(row,'Apply limits',self.rebuild)

        self.build_gamepad_tab(g)

        ttk.Label(s,text='Native Deforum schedules (advanced)',font=('Segoe UI',11,'bold')).pack(anchor='w')
        ttk.Label(s,text='Edit keyframes or sin/cos expressions.\nThese values are movement per frame, not positions.',wraplength=370).pack(anchor='w',pady=6)
        for label in LABELS:
            ttk.Label(s,text=label).pack(anchor='w')
            box=tk.Text(s,height=3,width=43,wrap='word');box.pack(fill='x',pady=(0,7));self.schedule_boxes.append(box)
        row=ttk.Frame(s);row.pack(fill='x');self.button(row,'Apply schedules',self.apply_schedules)
        ttk.Label(s,text='Expressions support t, pi, sin, cos, abs, sqrt, exp, log.\nUnsupported expressions are rejected; never executed.',wraplength=370).pack(anchor='w',pady=5)

        rsplit=ttk.Frame(right);rsplit.pack(fill='both',expand=True)
        rspec=ttk.Frame(rsplit,width=240);rspec.pack(side='right',fill='y');rspec.pack_propagate(False)
        rmain=ttk.Frame(rsplit);rmain.pack(side='left',fill='both',expand=True)
        row=ttk.Frame(rmain);row.pack(fill='x')
        ttk.Label(row,text='Deforum flat-depth geometry',font=('Segoe UI',12,'bold')).pack(side='left')
        self.button(row,'Load preview image',self.load_image)
        self.button(row,'Load rendered video',self.load_video)
        for text,val in (('Wireframe','wireframe'),('Video','video'),('Split','split')):
            ttk.Radiobutton(row,text=text,variable=self.preview_mode,value=val,command=self.on_preview_mode).pack(side='left')
        self.button(row,'Clear video',self.clear_video)
        ttk.Label(row,text='  Exaggerate preview: ').pack(side='left')
        self.variable('exaggerate', 1.0)
        ttk.Spinbox(row,textvariable=self.vars['exaggerate'],from_=1,to=20,increment=1,width=4,command=self.request_rebuild).pack(side='left')
        self.preview_frame=ttk.Frame(rmain);self.preview_frame.pack(fill='both',expand=True,pady=5)
        self.preview=tk.Canvas(self.preview_frame,bg='#101923',highlightthickness=0)
        self.preview_video=tk.Canvas(self.preview_frame,bg='#101923',highlightthickness=0)
        self.preview.pack(side='left',fill='both',expand=True)
        ttk.Label(rmain,text='Camera warp matched to your animation.py. AI regeneration and cadence blending are not simulated.').pack(anchor='w')
        row=ttk.Frame(rmain);row.pack(fill='x',pady=5)
        self.button(row,' ⏮',self.rewind);self.button(row,' Play',self.play);self.button(row,' Pause / Stop',self.stop);self.button(row,' Record',self.record);self.button(row,'Undo',self.undo)
        self.button(row,'Set A',self.set_loop_a);self.button(row,'Set B',self.set_loop_b)
        ttk.Checkbutton(row,text='Loop A-B',variable=self.loop_on).pack(side='left')
        self.position_label=tk.StringVar();ttk.Label(row,textvariable=self.position_label).pack(side='right')
        self.timeline=tk.DoubleVar(value=0)
        self.seek=ttk.Scale(rmain,from_=0,to=799,variable=self.timeline,command=self.scrub);self.seek.pack(fill='x')
        self.graph=tk.Canvas(rmain,height=170,bg='#101923',highlightthickness=0);self.graph.pack(fill='x',pady=5)
        self.graph.bind('<Button-1>',self.graph_seek)
        ttk.Label(rmain,text='Speed curves normalized by each axis limit | click the graph to seek').pack(anchor='w')
        ttk.Label(rspec,text='Spectrum (peak-hold)',font=('Segoe UI',11,'bold')).pack(anchor='w',pady=(2,0))
        ttk.Label(rspec,text='top = high freq · bottom = bass',font=('Segoe UI',8)).pack(anchor='w')
        self.spec_canvas=tk.Canvas(rspec,bg='#101923',highlightthickness=0);self.spec_canvas.pack(fill='both',expand=True,pady=6)
        self.spec_canvas.bind('<Configure>',lambda e:self.draw_spectrum())
        self.spec_canvas.bind('<Button-1>',self._spec_click)
        self.spec_canvas.bind('<Double-Button-1>',lambda e:self.guard(self._spec_new_channel))
        self.spec_readout=tk.StringVar(value='Click a peak to read its frequency')
        ttk.Label(rspec,textvariable=self.spec_readout,wraplength=220,font=('Segoe UI',8)).pack(anchor='w')
        ttk.Button(rspec,text='New channel at cursor',command=lambda:self.guard(self._spec_new_channel)).pack(fill='x',pady=6)

    def _load_settings(self):
        try:return json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
        except Exception:return {}
    def _save_settings(self,patch):
        d=self._load_settings();d.update(patch)
        try:SETTINGS_FILE.write_text(json.dumps(d),encoding='utf-8')
        except Exception:pass
    def apply_theme(self):
        dark=self.dark.get()
        self._save_settings({'dark_mode':bool(dark)})
        style=ttk.Style();style.theme_use('clam')
        if dark:
            bg='#2b2b2b';fg='#e8e8e8';field='#1e1e1e';btn='#3d3d3d';sel='#3a6ea5'
            style.configure('.',background=bg,foreground=fg,fieldbackground=field,
                            bordercolor='#5a5a5a',darkcolor='#1e1e1e',lightcolor='#4a4a4a',
                            troughcolor=field,selectbackground=sel,selectforeground='#ffffff')
            style.configure('TButton',background=btn,foreground=fg)
            style.map('TButton',background=[('active','#4d4d4d'),('disabled',bg)],foreground=[('disabled','#7a7a7a')])
            style.configure('TCheckbutton',background=bg,foreground=fg)
            style.map('TCheckbutton',background=[('active',bg)])
            style.configure('TRadiobutton',background=bg,foreground=fg)
            style.map('TRadiobutton',background=[('active',bg)])
            style.configure('TCombobox',fieldbackground=field,background=btn,foreground=fg,arrowcolor=fg)
            style.map('TCombobox',fieldbackground=[('readonly',field)],foreground=[('readonly',fg)])
            style.configure('TSpinbox',fieldbackground=field,background=btn,foreground=fg,arrowcolor=fg)
            style.configure('TNotebook',background=bg,borderwidth=0)
            style.configure('TNotebook.Tab',background='#3d3d3d',foreground=fg,padding=(10,4))
            style.map('TNotebook.Tab',background=[('selected','#1e1e1e')],foreground=[('selected','#ffffff')])
            style.configure('TScale',background=bg,troughcolor=field)
            style.configure('TSeparator',background='#5a5a5a')
            style.configure('TLabel',background=bg,foreground=fg)
            style.configure('TFrame',background=bg)
            style.configure('TLabelframe',background=bg,foreground=fg,bordercolor='#5a5a5a')
            style.configure('TLabelframe.Label',background=bg,foreground=fg)
            style.configure('TPanedwindow',background=bg)
            self.root.configure(bg=bg)
            self.root.option_add('*TCombobox*Listbox.background',field)
            self.root.option_add('*TCombobox*Listbox.foreground',fg)
            self.root.option_add('*TCombobox*Listbox.selectBackground',sel)
            self.root.option_add('*TCombobox*Listbox.selectForeground','#ffffff')
            for box in self.schedule_boxes:
                box.configure(bg=field,fg=fg,insertbackground=fg,selectbackground=sel,selectforeground='#ffffff')
            if hasattr(self,'channel_list'):
                self.channel_list.configure(bg=field,fg=fg,selectbackground=sel,selectforeground='#ffffff')
        else:
            # clam defaults were restored above; reset the tk widgets ttk doesn't own.
            self.root.configure(bg=style.lookup('TFrame','background') or '#f0f0f0')
            self.root.option_add('*TCombobox*Listbox.background','white')
            self.root.option_add('*TCombobox*Listbox.foreground','black')
            for box in self.schedule_boxes:
                box.configure(bg='white',fg='black',insertbackground='black',selectbackground='#3a6ea5',selectforeground='white')
            if hasattr(self,'channel_list'):
                self.channel_list.configure(bg='white',fg='black',selectbackground='#3a6ea5',selectforeground='white')
        self.draw_graph();self.draw_preview()
    def guard(self,func):
        try:func()
        except Exception as e:messagebox.showerror('Motion Studio',str(e))
    def values(self):
        return {k:v.get() for k,v in self.vars.items()}
    def snapshot(self):
        self.undo_stack.append((self.base.copy(),self.recorded.copy(),self.fps,copy.deepcopy(self.config),self.values(),[v.get() for v in self.limits],self._channel_params()))
        self.undo_stack=self.undo_stack[-15:]
    def _channel_params(self):
        return [{k:v for k,v in ch.items() if k not in ('env','times')} for ch in self.channels]
    def _restore_channels(self,params):
        # Reattach analyzed envelopes by channel name where they still exist.
        envs={ch['name']:(ch['env'],ch['times']) for ch in self.channels}
        self.channels=[]
        for p in params:
            ch=validate_channel(p)
            ch['env'],ch['times']=envs.get(ch['name'],(None,None))
            self.channels.append(ch)
        if hasattr(self,'channel_list'):
            self._refresh_channel_list();self._select_only(0);self._select_channel()
    def undo(self):
        if self.running:self.stop()
        if not self.undo_stack:return
        self.base,self.recorded,self.fps,self.config,values,limits,ch_params=self.undo_stack.pop()
        for k,v in values.items():self.vars[k].set(v)
        for var,value in zip(self.limits,limits):var.set(value)
        self._restore_channels(ch_params)
        self.frame=min(self.frame,len(self.base)-1);self.refresh_schedules();self.rebuild()
    def load_dialog(self):
        path=filedialog.askopenfilename(filetypes=[('Deforum settings','*.txt *.json'),('All files','*')])
        if path:self.load_path(path)
    def load_path(self,path,initial=False):
        try:data,tracks,repairs=load_config(path)
        except ValueError as e:
            # Offer the in-memory repair only if it actually fixes the file;
            # any other parse error is reported as-is.
            try:rdata,rtracks,rrepairs=load_config(path,repair=True)
            except ValueError:raise e
            if not rrepairs:raise e
            if not initial and not messagebox.askyesno('Repair schedule?','The motion schedule has a missing comma between keyframes.\nInsert the missing separator in the imported motion copy? Original file stays unchanged.'):return
            data,tracks,repairs=rdata,rtracks,rrepairs
        self.stop()
        if not initial:self.snapshot()
        self.config=data;self.base=tracks;self.recorded=np.zeros_like(tracks);self.fps=float(data['fps']);self.frame=0
        self.vars['fps'].set(self.fps);self.vars['frames'].set(len(tracks));self.vars['travel'].set(0)
        self.refresh_schedules();self.rebuild()
        self.info.set(f'{Path(path).name}  |  {data.get("W")} x {data.get("H")}  |  3D  |  cadence {data.get("diffusion_cadence")}  |  depth warping {data.get("use_depth_warping")}')
        if repairs:self.status.set('Imported baseline with missing comma repaired in '+', '.join(repairs)+'. Original file unchanged; rotations above 0.3 are limited in the composed output.')
    def refresh_schedules(self):
        for i,box in enumerate(self.schedule_boxes):
            box.delete('1.0','end')
            vals=self.base[:,i]
            box.insert('1.0',','.join(f'{j}:({vals[j]:.8f})' for j in keyframe_indices(vals)))
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
        if v['preset'] in ('Reveal drift','Fly-through') and self.vars['auto_music'].get():
            self._auto_music_channels()
    def _auto_music_channels(self):
        """Configure Bass pulse + Mid ripple channels for the music presets."""
        self.snapshot()
        bass=next((ch for ch in self.channels if ch['name'].lower()=='bass'),None)
        if bass is None:
            bass=validate_channel(dict(DEFAULT_CHANNELS[0]));self.channels.insert(0,bass)
        bass.update(enabled=True,axis=2,mode='Beat hits',low=35.0,high=180.0,
                    strength=2.0,attack=0.08,release=0.22,threshold=0.35,
                    offset=0.0,min_gap=0.18)
        ripple=next((ch for ch in self.channels if ch['name'].lower()=='mid ripple'),None)
        if ripple is None:
            ripple=validate_channel(dict(DEFAULT_CHANNELS[0],name='Mid Ripple'))
            ripple['env']=None;ripple['times']=None;ripple['peak_hz']=None
            self.channels.append(ripple)
        ripple.update(enabled=True,axis=2,mode='Ripple',low=1000.0,high=4000.0,
                      strength=1.5,attack=0.05,release=0.5,threshold=0.35,
                      offset=0.0,min_gap=0.25)
        self._refresh_channel_list()
        try:
            bi=self.channels.index(bass);self._select_only(bi);self._select_channel()
        except ValueError:pass
        for i,ch in enumerate(self.channels):
            if ch in (bass,ripple) and (ch['low'],ch['high'])!=tuple(ch.get('_band',(ch['low'],ch['high']))):
                ch['env']=None;ch['times']=None;ch['peak_hz']=None
                if self._audio_cache and self._audio_cache[0]==self.audio_path and self.audio_path:
                    self._reanalyze_channel(i)
        self.request_rebuild()
        if not self.audio_path:
            self.status.set('Motion + channels ready. Load a soundtrack and hit Sample soundtrack.')
        else:
            self.status.set('Bass pulse + Mid ripple channels set. Sample the soundtrack to attach them.')
    def apply_timing(self):
        fps=float(self.vars['fps'].get());n=int(self.vars['frames'].get())
        if not 1<=fps<=120 or not 2<=n<=30000:raise ValueError('Use 1–120 FPS and 2–30,000 frames')
        self.stop();self.snapshot();self.base=resample(self.base,self.fps,n,fps);self.recorded=resample(self.recorded,self.fps,n,fps)
        self.fps=fps;self.frame=0;self.refresh_schedules();self.rebuild()
        if self.audio_path:self.status.set('Timing changed. Re-analyze bass if the video duration was extended.')
    def rebuild(self):
        try:
            v=self.values();n=len(self.base)
            pulses=[]
            for ch in self.channels:
                if ch.get('enabled') and ch.get('env') is not None:
                    pulses.append((ch['axis'],channel_pulse(ch['env'],ch['times'],ch,n,self.fps)))
            self.output,scales,clipped=combine_multi(self.base+self.recorded,v['travel'],pulses,[x.get() for x in self.limits])
            ex=float(self.vars.get('exaggerate',tk.DoubleVar(value=1.0)).get())
            key=json.dumps({k:self.config.get(k) for k in ('W','H','use_depth_warping','enable_perspective_flip','shake_name','padding_mode','sampling_mode','fov_schedule','aspect_ratio_schedule','aspect_ratio_use_old_formula')},sort_keys=True)+str(n)
            if self.geometry is not None and key==self.geometry_key:self.geometry.update_tracks(self.output * ex)
            else:
                self.geometry=None;self.geometry_key=key
                try:self.geometry=GeometryPreview(self.config,(self.output * ex).copy(),self.preview_image)
                except ValueError as e:self.geometry_error=str(e)
            self.seek.configure(to=n-1)
            retained=min(scales.values()) if scales else 1.
            active=sum(1 for ch in self.channels if ch.get('enabled') and ch.get('env') is not None)
            self.status.set(f'{n/self.fps:.2f} seconds | {active} channel(s) active | Pulse retained {retained*100:.0f}%'+(' | Some base/recorded movement was limited' if clipped else '')+(' | Load audio + Re-analyze to enable sound channels' if active==0 and self.audio_path else ''))
            self.draw_graph();self.draw_preview();self.draw_spectrum();return True
        except Exception as e:
            self.status.set('Check controls: '+str(e));return False
    def load_audio(self):
        path=filedialog.askopenfilename(filetypes=[('Audio','*.mp3 *.wav *.flac *.ogg *.m4a'),('All files','*')])
        if path:self.audio_path=path;self.analyze_audio()
    def sample_soundtrack(self):
        """The original bass-pulse workflow: play the song while analyzing it."""
        if not self.audio_path:self.load_audio()
        if not self.audio_path:return
        self.frame=0;self.timeline.set(0)
        self.analyze_audio()
        self.play()
    def analyze_audio(self):
        if not self.audio_path:raise ValueError('Load a soundtrack first')
        if self.audio_busy:return
        self._cancel_pending_reanalyze()
        path=self.audio_path;duration=len(self.base)/self.fps
        active=[(i,ch) for i,ch in enumerate(self.channels) if ch.get('enabled')]
        if not active:raise ValueError('Enable at least one channel first')
        for _,ch in active:
            if not ch['low']<ch['high']:
                raise ValueError(f"Channel '{ch['name']}': low cutoff must be below high cutoff")
            if not ch['high']<CHANNEL_SR/2:
                raise ValueError(f"Channel '{ch['name']}': high cutoff must be below {CHANNEL_SR//2} Hz (soundtrack is analyzed at {CHANNEL_SR} Hz)")
        self.snapshot()
        self.audio_busy=True;self.audio_label.set('Analyzing soundtrack...')
        def worker():
            try:
                samples,sr=decode_audio(path,duration,CHANNEL_SR)
                out=[]
                for i,ch in active:
                    env,times=bass_envelope(samples,sr,ch['low'],ch['high'])
                    out.append((i,env,times))
                spec_f,spec_db=spectrum_db(samples,sr)
                # Keep the decoded audio: band edits re-filter from this cache
                # instead of decoding the file again.
                self._audio_cache=(path,samples,sr)
                self.messages.put(('audio',path,out,(spec_f,spec_db)))
            except Exception as e:self.messages.put(('error',str(e)))
        threading.Thread(target=worker,daemon=True).start()
    def load_image(self):
        path=filedialog.askopenfilename(filetypes=[('Images','*.png *.jpg *.jpeg *.webp')])
        if path:
            with Image.open(path) as im:self.preview_image=im.convert('RGB')
            self.geometry=None;self.rebuild()
    def load_video(self):
        path=filedialog.askopenfilename(filetypes=[('Video','*.mp4 *.mov *.avi *.mkv *.webm'),('All files','*')])
        if path:self.set_video(path)
    def set_video(self,path,quiet=False):
        """Load a rendered video for frame-synced review. Returns True on success."""
        try:v=VideoReview(path)
        except Exception as e:
            if not quiet:messagebox.showerror('Review video',str(e))
            return False
        if self.video:
            try:self.video.close()
            except Exception:pass
        self.video=v;self.video_path=str(path)
        notes=[]
        if v.fps and abs(v.fps-self.fps)>0.5:
            notes.append(f'Video is {v.fps:.2f} fps but the project is {self.fps:g} fps — frames map 1:1 by index, so picture may drift from the motion.')
        if v.n!=len(self.base):
            notes.append(f'Video has {v.n} frames, project has {len(self.base)} — past the shorter end the last available frame holds.')
        if notes and not quiet:messagebox.showwarning('Review video','\n\n'.join(notes))
        self.preview_mode.set('video');self._video_key=None;self._video_ok=False;self.draw_preview()
        self.status.set(f'Review video: {Path(path).name} ({v.n} frames @ {v.fps:.2f} fps)')
        return True
    def on_preview_mode(self):
        if self.preview_mode.get() in ('video','split') and self.video is None:
            self.load_video()
            if self.video is None:self.preview_mode.set('wireframe')
        self.draw_preview()
    def _preview_layout(self):
        # Re-pack only on mode change: re-packing fires <Configure>, which
        # calls draw_preview again — without this guard that recurses.
        mode=self.preview_mode.get()
        if mode==self._preview_mode_shown:return
        self._preview_mode_shown=mode
        self.preview.pack_forget();self.preview_video.pack_forget()
        if mode=='video':
            self.preview_video.pack(side='left',fill='both',expand=True)
        elif mode=='split':
            self.preview.pack(side='left',fill='both',expand=True)
            self.preview_video.pack(side='left',fill='both',expand=True)
        else:
            self.preview.pack(side='left',fill='both',expand=True)
    def clear_video(self):
        if self.video:
            try:self.video.close()
            except Exception:pass
        self.video=None;self.video_path='';self.preview_mode.set('wireframe')
        self._video_key=None;self._video_ok=False;self.draw_preview()
        self.status.set('Review video cleared')
    def set_loop_a(self):
        self.loop_a=self.frame
        if self.loop_b is not None and self.loop_b<=self.loop_a:self.loop_b=None
        self.status.set(f'Loop start: frame {self.loop_a}');self.draw_preview()
    def set_loop_b(self):
        if self.loop_a is None or self.frame<=self.loop_a:
            self.status.set('Set loop start (A) first, then move past it and set B');return
        self.loop_b=self.frame
        self.status.set(f'Loop: frames {self.loop_a}-{self.loop_b} — enable "Loop A-B" to repeat');self.draw_preview()
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
                result=self.messages.get_nowait()
                if result[0]=='audio':
                    self.audio_busy=False
                    _,path,items,spec=result
                    spec_f,spec_db=np.asarray(spec[0],float),np.asarray(spec[1],float)
                    self.spectrum=(spec_f,spec_db);self.spec_cursor_f=None
                    for i,env,times in items:
                        if i<len(self.channels):
                            ch=self.channels[i]
                            ch['env'],ch['times']=env,times
                            ch['_band']=(ch['low'],ch['high'])
                            m=(spec_f>=ch['low'])&(spec_f<=ch['high'])
                            ch['peak_hz']=float(spec_f[m][np.argmax(spec_db[m])]) if np.any(m) else None
                    self.audio_label.set(f'{Path(path).name} | analyzed {len(items)} channel(s)')
                    self.rebuild()
                elif result[0]=='channel':
                    _,i,env,times,peak,lo,hi=result
                    if 0<=i<len(self.channels):
                        ch=self.channels[i]
                        ch['env'],ch['times']=env,times
                        ch['peak_hz']=peak;ch['_band']=(lo,hi)
                        self.status.set(f"Channel '{ch['name']}' re-analyzed ({lo:.0f}–{hi:.0f} Hz)")
                    self.rebuild()
                else:
                    self.audio_busy=False;self.audio_label.set('Analysis failed');messagebox.showerror('Audio',result[1])
        except queue.Empty:pass
        # Debounced rebuilds: at most one per tick, never re-entrant with playback.
        if self._rebuild_pending:
            self._rebuild_pending=False;self.rebuild()
        try:
            raw=self.controller.read_raw();self.pad_label.set(raw['label'])
            buttons=raw['buttons']
            pressed=buttons&~self.last_buttons;self.last_buttons=buttons
            if pressed:
                for b,bit in BUTTON_BITS.items():
                    if pressed&bit:
                        act=button_action(self.pad_map,b)
                        if act=='record':self.guard(self.record)
                        elif act=='stop':self.stop()
                        elif act=='undo':
                            if not self.running:self.undo()
                        elif act=='play':
                            if not self.running:self.guard(self.play)
            padv=pad_axis_target(raw,self.pad_map,float(self.vars['deadzone'].get()),
                                 float(self.vars['expo'].get()))
            manual=np.clip(np.array([v.get() for v in self.manual]),-1,1)
            lim=np.array([v.get() for v in self.limits])
            target=np.clip(padv+manual*float(self.vars['sensitivity'].get())*lim,-lim,lim)
            if hasattr(self,'pad_test_vars'):
                for inp,var in self.pad_test_vars.items():
                    if inp in raw:var.set(f'{raw[inp]:+.2f}')
                for b,var in self.pad_btn_test_vars.items():
                    var.set('●' if buttons&BUTTON_BITS[b] else '○')
            alpha=1-math.exp(-dt/max(.01,float(self.vars['smooth'].get())))
            self.pad_smooth+=(target-self.pad_smooth)*alpha
            if self.running:
                next_frame=min(len(self.base)-1,int((now-self.clock)*self.fps))
                if (self.loop_on.get() and not self.recording and self.loop_a is not None
                        and self.loop_b is not None and self.loop_a<self.loop_b
                        and next_frame>self.loop_b):
                    next_frame=self.loop_a;self.clock=now-self.loop_a/self.fps
                if self.recording:
                    enabled=np.array([v.get() for v in self.enabled_axes])
                    for f in range(self.last_record+1,next_frame+1):
                        self.recorded[f,enabled]=self.pad_smooth[enabled]
                    self.last_record=next_frame
                self.frame=next_frame;self.timeline.set(self.frame)
                if self.recording:self.request_rebuild()
                else:self.draw_preview()
                if self.frame==len(self.base)-1:self.stop();self.status.set('Playback / recording finished. Review the curves and export when ready.')
        except Exception as e:self.status.set(str(e));self.stop()
        self.root.after(20,self.tick)

    def rewind(self):
        self.stop()
        self.frame=0;self.timeline.set(0);self.draw_preview()

    def draw_preview(self):
        self._preview_layout()
        mode=self.preview_mode.get()
        f=min(self.frame,len(self.output)-1)
        if mode in ('wireframe','split'):self._draw_wireframe(f)
        if mode in ('video','split'):self._draw_video_panel(f)
        loop=f'   |   Loop {self.loop_a}-{self.loop_b}' if self.loop_a is not None and self.loop_b is not None else ''
        self.position_label.set(f'Frame {f} / {len(self.output)-1}   |   {f/self.fps:.2f}s'+('   RECORDING' if self.recording else '')+loop)
        self.draw_graph(cursor_only=True)
    def _draw_wireframe(self,f):
        c=self.preview;c.delete('all');w=max(100,c.winfo_width());h=max(100,c.winfo_height())
        if self.geometry is not None:
            try:
                im, done = self.geometry.seek(f, max_steps=4 if not self.running else 1)
                im.thumbnail((max(16,w-30),max(16,h-50)),Image.Resampling.LANCZOS)
                self.tk_image=ImageTk.PhotoImage(im);c.create_image(w/2,h/2+10,image=self.tk_image)
                title=f'Native {self.geometry.width}x{self.geometry.height} | FOV {self.geometry.fov[self.geometry.last]:.1f} | aspect {self.geometry.aspect[self.geometry.last]:.3f} | frame-by-frame warp'
                if not done:
                    title += f' (computing {self.geometry.last}/{f}...)'
                    # Catch-up chain: at most one scheduled at a time, and never
                    # while playing (tick() already redraws every frame). Without
                    # this guard, overlapping chains flood the event queue and the
                    # UI freezes after mid-playback setting changes.
                    if not self.running and not self._preview_queued:
                        self._preview_queued=True
                        self.root.after(10,self._draw_preview_soon)
            except ValueError as e:title=str(e)
        else:title=getattr(self,'geometry_error','Loading geometry...')
        c.create_text(12,12,anchor='nw',fill='white',width=max(80,w-24),text=title)
    def _draw_video_panel(self,f):
        c=self.preview_video;c.delete('all');w=max(100,c.winfo_width());h=max(100,c.winfo_height())
        if self.video is None:
            c.create_text(12,12,anchor='nw',fill='white',text='Load a rendered video to review it here');return
        key=(f,w,h)
        if key!=self._video_key:
            # Re-decode only when the frame or panel size changed; repeated
            # draw_preview calls (tweaks while paused) reuse the image.
            self._video_key=key;self._video_ok=False
            rgb=self.video.frame(f)
            if rgb is not None:
                im=Image.fromarray(rgb)
                im.thumbnail((max(16,w-30),max(16,h-50)),Image.Resampling.LANCZOS)
                self.tk_video=ImageTk.PhotoImage(im);self._video_ok=True
            else:self._video_key=None
        if self._video_ok:
            c.create_image(w/2,h/2+10,image=self.tk_video)
            title=f'Rendered video: {Path(self.video_path).name} | frame {f}'
        else:title='Review video: frame unavailable'
        c.create_text(12,12,anchor='nw',fill='white',width=max(80,w-24),text=title)
    def _draw_preview_soon(self):
        self._preview_queued=False
        if not self.running:self.draw_preview()
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
    def draw_spectrum(self):
        # Vertical analyzer: top = highest frequency, bottom = bass.
        c=self.spec_canvas;c.delete('all')
        w=max(120,c.winfo_width());h=max(140,c.winfo_height())
        if self.spectrum is None:
            c.create_text(w/2,h/2,fill='#8a9bb0',width=w-16,
                          text='Load a soundtrack and click Re-analyze channels to see its spectrum')
            return
        freqs,db=self.spectrum;fmin,fmax=float(freqs[0]),float(freqs[-1])
        span=math.log10(fmax)-math.log10(fmin)
        top,bottom=10,h-24;left,right=32,w-8
        def Y(f):return top+(math.log10(fmax)-math.log10(max(f,fmin)))/span*(bottom-top)
        def X(v):return left+(max(v,-80)+80)/80*(right-left)
        for v in (-60,-40,-20):
            x=X(v);c.create_line(x,top,x,bottom,fill='#232d38')
            c.create_text(x,top-2,anchor='s',fill='#4a5a6b',font=('Segoe UI',6),text=str(v))
        for f in (50,100,200,500,1000,2000,5000,10000):
            if fmin<=f<=fmax:
                y=Y(f);c.create_line(left,y,right,y,fill='#2a3542')
                c.create_text(2,y,anchor='w',fill='#5a6b7d',font=('Segoe UI',7),
                              text=f'{f//1000}k' if f>=1000 else str(f))
        for i,ch in enumerate(self.channels):
            if not ch.get('enabled'):continue
            col=CHANNEL_COLORS[i%len(CHANNEL_COLORS)]
            y0,y1=Y(min(ch['high'],fmax)),Y(max(ch['low'],fmin))
            if y1>y0:c.create_rectangle(left,y0,right,y1,fill=col,stipple='gray12',outline='')
            pk=ch.get('peak_hz')
            if pk and ch['low']<=pk<=ch['high'] and fmin<=pk<=fmax:
                yp=Y(pk);c.create_line(left,yp,right,yp,fill=col,width=2)
                c.create_text(left+3,yp-9,anchor='w',fill=col,font=('Segoe UI',7,'bold'),
                              text=f'{pk:,.0f} Hz')
        pts=[]
        for f,v in zip(freqs,db):pts.extend((X(v),Y(f)))
        if pts:c.create_line(*pts,fill='#e8e8e8',width=1)
        if self.spec_cursor_f and fmin<=self.spec_cursor_f<=fmax:
            yc=Y(self.spec_cursor_f)
            c.create_line(left,yc,right,yc,fill='#ffffff',width=1,dash=(3,2))
            c.create_text(left+3,yc+10,anchor='w',fill='#ffffff',font=('Segoe UI',7,'bold'),
                          text=f'{self.spec_cursor_f:,.0f} Hz')
    def _spec_click(self,event):
        if self.spectrum is None:return
        freqs,db=self.spectrum;fmin,fmax=float(freqs[0]),float(freqs[-1])
        h=max(140,self.spec_canvas.winfo_height())
        top,bottom=10,h-24
        span=math.log10(fmax)-math.log10(fmin)
        f=10**(math.log10(fmax)-(event.y-top)/max(1,bottom-top)*span)
        f=min(max(f,fmin),fmax)
        self.spec_cursor_f=f
        v=float(np.interp(f,freqs,db))
        self.spec_readout.set(f'{f:,.0f} Hz · {freq_class(f)} · {v:.1f} dB — double-click (or the button) to make this a channel')
        self.draw_spectrum()
    def _spec_new_channel(self):
        f=self.spec_cursor_f
        if f is None:raise ValueError('Click a peak in the spectrum first')
        self.snapshot()
        lo=max(10,f/math.sqrt(2));hi=min(CHANNEL_SR/2,f*math.sqrt(2))
        ch=validate_channel({'name':f'Peak {f:,.0f} Hz','enabled':True,'axis':2,
                             'mode':'Beat hits','low':lo,'high':hi,
                             'strength':1.0,'attack':0.05,'release':0.2,
                             'threshold':0.35,'offset':0.0,'min_gap':0.15,
                             'start':0.0,'end':0.0})
        ch['env']=None;ch['times']=None;ch['peak_hz']=None
        self.channels.append(ch)
        self._refresh_channel_list();self._select_only(len(self.channels)-1);self._select_channel()
        self.request_rebuild()
        self.status.set(f'New channel around {f:,.0f} Hz ({freq_class(f)}). Pick its target axis, then Re-analyze channels.')

    def save_project(self):
        path=filedialog.asksaveasfilename(defaultextension='.dms.json',filetypes=[('Motion Studio project','*.dms.json')])
        if not path:return
        channels=[]
        for ch in self.channels:
            d={k:v for k,v in ch.items() if k not in ('env','times')}
            d['env']=None if ch['env'] is None else np.asarray(ch['env'],float).tolist()
            d['times']=None if ch['times'] is None else np.asarray(ch['times'],float).tolist()
            channels.append(d)
        data={'format':'deforum-motion-studio','version':2,'config':self.config,'base':self.base.tolist(),'recorded':self.recorded.tolist(),'fps':self.fps,'controls':self.values(),'limits':[v.get() for v in self.limits],'audio_path':self.audio_path,'video_path':self.video_path,'channels':channels,
              'spectrum':None if self.spectrum is None else {'f':np.asarray(self.spectrum[0],float).tolist(),'db':np.asarray(self.spectrum[1],float).tolist()}}
        Path(path).write_text(json.dumps(data,indent=2),encoding='utf-8');self.status.set('Project saved, including channel analysis. Back up the soundtrack separately.')
    def _migrate_v1_channels(self,d):
        """Build channel list from a v0.2 (single bass pulse) project."""
        controls=d.get('controls',{})
        ch=validate_channel({
            'name':'Bass','enabled':bool(controls.get('pulse_enabled',False)),'axis':2,
            'mode':controls.get('pulse_mode','Beat hits'),
            'low':controls.get('low',35),'high':controls.get('high',180),
            'strength':controls.get('pulse_strength',2),'attack':controls.get('attack',.08),
            'release':controls.get('release',.22),'threshold':controls.get('threshold',.35),
            'offset':controls.get('offset',0),'min_gap':0.18,'start':0.0,'end':0.0})
        if d.get('bass') is not None:
            ch['env']=np.asarray(d['bass'],float);ch['times']=np.asarray(d['bass_times'],float)
        else:ch['env']=None;ch['times']=None
        ch['peak_hz']=None
        return [ch]
    def _load_channels(self,d):
        raw_channels=d.get('channels')
        if isinstance(raw_channels,list) and raw_channels:
            out=[]
            for raw in raw_channels:
                ch=validate_channel(raw)
                ch['env']=np.asarray(raw['env'],float) if raw.get('env') is not None else None
                ch['times']=np.asarray(raw['times'],float) if raw.get('times') is not None else None
                if (ch['env'] is None)!=(ch['times'] is None):raise ValueError('Channel analysis is incomplete')
                out.append(ch)
            return out
        if not isinstance(raw_channels,list):
            return self._migrate_v1_channels(d)
        # Empty channel list: fall back to defaults rather than a broken project.
        return [dict(validate_channel(dc),env=None,times=None,peak_hz=None) for dc in DEFAULT_CHANNELS]
    def open_project(self):
        path=filedialog.askopenfilename(filetypes=[('Motion Studio project','*.dms.json')])
        if not path:return
        d=json.loads(Path(path).read_text(encoding='utf-8'))
        if d.get('format')!='deforum-motion-studio':raise ValueError('Not a Motion Studio project')
        base=np.asarray(d['base'],float);recorded=np.asarray(d['recorded'],float)
        if base.ndim!=2 or base.shape[1]!=6 or base.shape!=recorded.shape or not 2<=len(base)<=30000 or not np.all(np.isfinite(base+recorded)):raise ValueError('Invalid project motion')
        fps=float(d['fps'])
        if not 1<=fps<=120:raise ValueError('Invalid project FPS')
        limits=d.get('limits')
        if not isinstance(limits,list) or len(limits)!=6 or not all(isinstance(x,(int,float)) and 0<x<=30 for x in limits):
            raise ValueError('Invalid project limits')
        channels=self._load_channels(d)
        spec=d.get('spectrum')
        try:
            self.spectrum=(np.asarray(spec['f'],float),np.asarray(spec['db'],float)) if isinstance(spec,dict) and spec.get('f') and spec.get('db') else None
        except Exception:self.spectrum=None
        self.spec_cursor_f=None
        if hasattr(self,'spec_readout'):self.spec_readout.set('Click a peak in the spectrum to read its frequency')
        self.stop();self.snapshot();self.config=d['config'];self.base=base;self.recorded=recorded;self.fps=fps;self.frame=0
        for k,v in d.get('controls',{}).items():
            if k in self.vars:self.vars[k].set(v)
        for var,value in zip(self.limits,limits):var.set(value)
        self.channels=channels
        if hasattr(self,'channel_list'):
            self._refresh_channel_list();self._select_only(0);self._select_channel()
        self.audio_path=d.get('audio_path','')
        self._cancel_pending_reanalyze()
        if self._audio_cache and self._audio_cache[0]!=self.audio_path:self._audio_cache=None
        self.audio_label.set(Path(self.audio_path).name or 'No soundtrack')
        vp=d.get('video_path','') or ''
        if vp and Path(vp).is_file():self.set_video(vp,quiet=True)
        else:
            if self.video:
                try:self.video.close()
                except Exception:pass
            self.video=None;self.video_path='';self.preview_mode.set('wireframe')
        self.refresh_schedules();self.rebuild();self.info.set('Project: '+Path(path).name)
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
        if self.audio_path:
            missing=[ch['name'] for ch in self.channels if ch.get('enabled') and ch.get('env') is None]
            if missing:
                if not messagebox.askyesno('Export without pulse effects?',
                        'These enabled sound channels have not been analyzed yet:\n- '+'\n- '.join(missing)+
                        '\n\nTheir pulse effects will be MISSING from the exported file.\nExport anyway?'):
                    self.status.set('Export cancelled — click Re-analyze channels first');return
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
        self._cancel_pending_reanalyze();self.stop()
        if self.video:
            try:self.video.close()
            except Exception:pass
        self.root.destroy()

if __name__=='__main__':
    root=tk.Tk();app=App(root);root.mainloop()

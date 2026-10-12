"""Headless GUI smoke test for Deforum Motion Studio 0.3 (run under xvfb)."""
import json, tempfile, sys
from pathlib import Path
from unittest import mock
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import tkinter as tk
from tkinter import filedialog, messagebox
import app as appmod
from motion import load_config, AXES
from controller import button_action

failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: failures.append(name)

root = tk.Tk()
a = appmod.App(root)
root.update()

# 1. default channels
check("3 default channels", len(a.channels) == 3)
check("bass enabled, others disabled",
      a.channels[0]['enabled'] and not a.channels[1]['enabled'] and not a.channels[2]['enabled'])
check("channel axes", [c['axis'] for c in a.channels] == [2, 4, 5])
check("listbox populated", a.channel_list.size() == 3)

# 2. rebuild works with no audio
check("rebuild ok", a.rebuild() is True)
check("output shape", a.output.shape == (800, 6))

# 3. edit a channel param -> request_rebuild flag, then tick drains it
a.channel_list.select_clear(0, 'end'); a.channel_list.select_set(1); a._select_channel()
a.ch_vars['enabled'].set(True)
check("edit sets rebuild pending", a._rebuild_pending is True)
a.ch_vars['strength'].set(1.5)
check("channel updated", a.channels[1]['enabled'] is True and a.channels[1]['strength'] == 1.5)
a._rebuild_pending = False  # consumed manually; tick() would drain it within 20ms

# 4. add / duplicate / remove
n0 = len(a.channels)
a._add_channel()
check("add channel", len(a.channels) == n0 + 1)
a._duplicate_channel()
check("duplicate channel", len(a.channels) == n0 + 2)
a._remove_channel(); a._remove_channel()
check("remove channels", len(a.channels) == n0)
try:
    while len(a.channels) > 1: a._remove_channel()
    a._remove_channel(); check("remove last blocked", False)
except ValueError:
    check("remove last blocked", True)

# 5. dark mode toggle both ways
a.dark.set(False); a.apply_theme(); root.update()
check("light mode", True)
a.dark.set(True); a.apply_theme(); root.update()
check("dark mode", True)
check("settings persisted", json.loads(appmod.SETTINGS_FILE.read_text())['dark_mode'] is True)

# 6. project save/open roundtrip (v2)
a.reset_channels()
a._refresh_channel_list(); a._select_only(0); a._select_channel()
with tempfile.TemporaryDirectory() as tmp:
    p = str(Path(tmp)/'proj.dms.json')
    with mock.patch.object(filedialog, 'asksaveasfilename', return_value=p):
        a.save_project()
    d = json.loads(Path(p).read_text())
    check("project v2", d['version'] == 2 and len(d['channels']) == 3)
    a.channels[0]['name'] = 'Mangled'
    with mock.patch.object(filedialog, 'askopenfilename', return_value=p):
        a.open_project()
    check("project roundtrip name", a.channels[0]['name'] == 'Bass')
    check("project roundtrip count", len(a.channels) == 3)

# 7. v1 migration
with tempfile.TemporaryDirectory() as tmp:
    v1 = {'format':'deforum-motion-studio','version':1,'config':a.config,
          'base':a.base.tolist(),'recorded':a.recorded.tolist(),'fps':24,
          'controls':{'pulse_enabled':True,'pulse_mode':'Beat hits','low':35,'high':180,
                      'pulse_strength':2,'attack':0.08,'release':0.22,'threshold':0.35,'offset':0},
          'limits':[.3,.3,1.75,.3,.3,.3],'audio_path':'',
          'bass':[0.1]*50,'bass_times':[i/100 for i in range(50)]}
    p = str(Path(tmp)/'old.dms.json'); Path(p).write_text(json.dumps(v1))
    with mock.patch.object(filedialog, 'askopenfilename', return_value=p):
        a.open_project()
    check("v1 migrated to 1 channel", len(a.channels) == 1 and a.channels[0]['name'] == 'Bass')
    check("v1 env restored", a.channels[0]['env'] is not None and len(a.channels[0]['env']) == 50)
    check("v1 enabled restored", a.channels[0]['enabled'] is True)

# 8. undo restores channel params
a.reset_channels()
if hasattr(a, 'channel_list'): a._refresh_channel_list(); a.channel_list.select_set(0); a._select_channel()
a.snapshot()
a.channels[0]['strength'] = 9.0
a.undo()
check("undo restores channel", abs(a.channels[0]['strength'] - 2.0) < 1e-9)

# 9. export -> reload roundtrip within tolerance
with tempfile.TemporaryDirectory() as tmp:
    p = str(Path(tmp)/'out.txt')
    with mock.patch.object(filedialog, 'asksaveasfilename', return_value=p):
        with mock.patch.object(messagebox, 'showerror'):
            a.export()
    cfg, tracks, _ = load_config(p)
    err = np.max(np.abs(tracks - a.output))
    check(f"export roundtrip err {err:.2e} < 1e-3", err < 1e-3)
    # count keys vs frames on a constant axis
    raw = json.loads(Path(p).read_text())
    nkeys = sum(len(raw[ax].split('),')) for ax in AXES)
    check(f"export reduced keys ({nkeys} < {800*6})", nkeys < 800*6)

# 10. freeze guards: preview chain single-scheduled, tick drains rebuild
a._rebuild_pending = True
a.tick()  # tick reschedules itself; call once synchronously
root.update()
check("tick drained rebuild", a._rebuild_pending is False)
a.play(); root.update(); a.stop()
check("play/stop cycle", not a.running)
a.rewind(); root.update()
check("rewind ok", a.frame == 0 and not a.running)

# 11. load_path repair still works (missing comma baseline)
with mock.patch.object(messagebox, 'askyesno', return_value=True):
    a.load_path(Path(__file__).parent/'examples'/'working_baseline.txt')
check("baseline loads with repair", a.base.shape == (800, 6))

# 12. spectrum click-to-inspect + new channel at cursor
from motion import spectrum_db
sr = 22050; t = np.arange(sr*2)/sr
f, db = spectrum_db(np.sin(2*np.pi*440*t), sr)
a.spectrum = (f, db); a.draw_spectrum(); root.update()
class FakeEvent: pass
e = FakeEvent()
a.spec_canvas.update()
_ch = a.spec_canvas.winfo_height()
check("spectrum sidebar is tall", _ch > 400)
e.y = max(30, int(_ch*0.15))  # near the top -> high frequency
a._spec_click(e)
check("spectrum click sets cursor", a.spec_cursor_f is not None and 25 <= a.spec_cursor_f <= 11025)
check("top of spectrum = high freq", a.spec_cursor_f > 2000)
check("spectrum readout shows Hz", 'Hz' in a.spec_readout.get())
e.y = int(_ch*0.9)  # near the bottom -> bass
a._spec_click(e)
check("bottom of spectrum = bass", a.spec_cursor_f < 250)
e.y = max(30, int(_ch*0.15)); a._spec_click(e)  # back to top for channel creation
n0 = len(a.channels)
a._spec_new_channel()
check("new channel at cursor", len(a.channels) == n0+1 and 'Hz' in a.channels[-1]['name'])
ch = a.channels[-1]
check("channel band brackets cursor", ch['low'] < a.spec_cursor_f < ch['high'])
a.draw_spectrum(); root.update()
check("spectrum redraws with cursor", True)

# 13. band change auto re-analyzes from cached audio (stale envelope fix)
import time as _time
mp3 = '/home/hatch/workspace/your_files/channel-test.mp3'
a.audio_path = mp3
with mock.patch.object(messagebox, 'showerror'):
    a.analyze_audio()
    for _ in range(200):
        root.update(); _time.sleep(0.05); a.tick()
        if a.channels[0]['env'] is not None: break
check("full analysis sets env", a.channels[0]['env'] is not None)
check("audio cached", a._audio_cache is not None and a._audio_cache[0] == mp3)
check("band recorded", a.channels[0].get('_band') == (a.channels[0]['low'], a.channels[0]['high']))
old_env = a.channels[0]['env'].copy()
a._select_only(0); a._select_channel()
a.ch_vars['low'].set(1315)   # transient invalid (low>high): no change committed
check("transient invalid band ignored", a.channels[0]['env'] is not None)
a.ch_vars['high'].set(1581)  # now valid: stale envelope must drop immediately
check("stale env cleared on band change", a.channels[0]['env'] is None)
with mock.patch.object(messagebox, 'showerror'):
    for _ in range(200):
        root.update(); _time.sleep(0.05); a.tick()
        if a.channels[0]['env'] is not None: break
check("band re-analyzed", a.channels[0]['env'] is not None)
check("new band recorded", a.channels[0].get('_band') == (1315, 1581))
check("envelope actually changed", not np.allclose(a.channels[0]['env'], old_env))

# 14. export warns when an enabled channel was never analyzed
from motion import decode_audio, CHANNEL_SR
_smp,_sr = decode_audio(mp3, 2)
check("decode defaults to CHANNEL_SR", _sr == CHANNEL_SR)
a._add_channel()
check("new channel has no envelope", a.channels[-1]['env'] is None and a.channels[-1]['enabled'])
with mock.patch.object(messagebox,'askyesno',return_value=False) as _mq, \
     mock.patch.object(filedialog,'asksaveasfilename') as _mf:
    a.export()
    check("export asks about missing pulse", _mq.called)
    check("export cancelled skips file dialog", not _mf.called)
check("export cancelled status", 'Re-analyze' in a.status.get())
with mock.patch.object(messagebox,'askyesno',return_value=True), \
     mock.patch.object(filedialog,'asksaveasfilename',return_value='/tmp/smoke_export.txt'):
    a.export()
    check("export anyway writes file", Path('/tmp/smoke_export.txt').exists())

# 15. sample soundtrack: analyzes + starts playback (original v0.2 workflow)
with mock.patch.object(appmod.App,'play') as _mp:
    a.audio_path = mp3
    a.sample_soundtrack()
    check("sample triggers analysis", a.audio_busy)
    check("sample starts playback", _mp.called)

# 16. music presets auto-configure Bass pulse + Mid ripple channels
a.vars['preset'].set('Fly-through');a.vars['auto_music'].set(True)
a.apply_preset()
bass=[ch for ch in a.channels if ch['name'].lower()=='bass']
check("bass channel present+enabled", len(bass)==1 and bass[0]['enabled'] and
      (bass[0]['low'],bass[0]['high'])==(35.0,180.0) and bass[0]['mode']=='Beat hits')
rip=[ch for ch in a.channels if ch['name'].lower()=='mid ripple']
check("mid ripple channel created", len(rip)==1 and rip[0]['enabled'] and rip[0]['mode']=='Ripple')
check("ripple band+axis", (rip[0]['low'],rip[0]['high'])==(1000.0,4000.0) and rip[0]['axis']==2)
# preset motion actually applied: fly-through pushes forward on Z
check("fly-through motion applied", float(np.mean(a.base[60:180,2]))>0)
# second apply must not duplicate the ripple channel
a.apply_preset()
check("no duplicate ripple channel", len([ch for ch in a.channels if ch['name'].lower()=='mid ripple'])==1)

# 13. gamepad tab: mapping UI, live test readouts, per-control max, persistence
saved_settings = appmod.SETTINGS_FILE.read_text() if appmod.SETTINGS_FILE.exists() else None
try:
    check("pad tab built", hasattr(a, 'pad_vars') and 'lx' in a.pad_vars and 'btn_a' in a.pad_vars)
    check("default lx target", a.pad_vars['lx']['target'].get() == 'Move sideways')
    check("default lx max", abs(a.pad_vars['lx']['max'].get() - 0.3) < 1e-9)
    check("default A action", a.pad_vars['btn_a']['target'].get() == 'Record')
    check("default bumpers roll", a.pad_vars['btn_lb']['target'].get() == 'Roll'
          and a.pad_vars['btn_rb']['target'].get() == 'Roll'
          and abs(a.pad_vars['btn_rb']['max'].get() - 0.3) < 1e-9)
    check("expo default linear", abs(a.vars['expo'].get() - 1.0) < 1e-9)
    # remap via UI: left stick X -> Travel / distance, max 0.5, inverted
    a.pad_vars['lx']['target'].set('Travel / distance')
    a.pad_vars['lx']['max'].set(0.5)
    a.pad_vars['lx']['invert'].set(True)
    root.update()
    check("remap stored", a.pad_map['lx'] == {'target': 'axis:2', 'max': 0.5, 'invert': True})
    d = json.loads(appmod.SETTINGS_FILE.read_text())
    check("pad map persisted", d['pad_map']['lx']['target'] == 'axis:2' and d['pad_map']['lx']['max'] == 0.5)
    # button remap via UI
    a.pad_vars['btn_y']['target'].set('Play'); root.update()
    check("button remap stored", a.pad_map['btn_y'] == {'target': 'action:play', 'max': 0.0, 'invert': False})
    # button -> axis remap via UI (hold X to tilt)
    a.pad_vars['btn_x']['target'].set('Tilt up / down'); root.update()
    a.pad_vars['btn_x']['max'].set(0.4); root.update()
    check("button axis remap", a.pad_map['btn_x']['target'] == 'axis:3'
          and abs(a.pad_map['btn_x']['max'] - 0.4) < 1e-9)
    check("button no action when axis", button_action(a.pad_map, 'x') == '')
    # live test readouts with a fake controller
    fake = {'lx': 1.0, 'ly': 0.0, 'rx': 0.0, 'ry': 0.0, 'lt': 0.0, 'rt': 0.0,
            'buttons': 0x4000, 'label': 'fake'}
    with mock.patch.object(a.controller, 'read_raw', return_value=fake):
        a.tick(); root.update()
    check("live stick value", a.pad_test_vars['lx'].get() == '+1.00')
    check("live button pressed", a.pad_btn_test_vars['x'].get() == '●')
    check("live button idle", a.pad_btn_test_vars['y'].get() == '○')
    # mapped button action fires: X is Undo by default; empty stack -> silent no-op
    check("undo no-op safe", a.undo() is None)
    # reset restores defaults in UI and settings
    a._reset_pad_map(); root.update()
    check("reset restores lx", a.pad_vars['lx']['target'].get() == 'Move sideways'
          and abs(a.pad_vars['lx']['max'].get() - 0.3) < 1e-9)
finally:
    if saved_settings is not None:
        appmod.SETTINGS_FILE.write_text(saved_settings)

# 14. review video: load, view toggle, frame sync, A/B loop, project roundtrip
import cv2 as _cv2, time as _time
with tempfile.TemporaryDirectory() as tmp:
    p=str(Path(tmp)/'rev.mp4')
    _vw=_cv2.VideoWriter(p,_cv2.VideoWriter_fourcc(*'mp4v'),24,(96,64))
    for i in range(60):_vw.write(np.full((64,96,3),(i*3%256,0,0),np.uint8))
    _vw.release()
    check("video loads", a.set_video(p,quiet=True) and a.video is not None and a.video.n==60)
    check("video mode on", a.preview_mode.get()=='video')
    root.update()
    check("video canvas shown", a.preview_video.winfo_ismapped() and not a.preview.winfo_ismapped())
    a.tk_video=None;a.frame=10;a.draw_preview();root.update()
    check("video frame rendered", a.tk_video is not None and a.tk_video.width()>0)
    # split view shows both panels side by side
    a.preview_mode.set('split');a.on_preview_mode();root.update()
    check("split shows both", a.preview.winfo_ismapped() and a.preview_video.winfo_ismapped())
    a.frame=20;a.draw_preview();root.update()
    check("split draws both", a.tk_image is not None and a.tk_video is not None)
    a.preview_mode.set('wireframe');a.on_preview_mode();root.update()
    check("wireframe mode hides video", a.preview.winfo_ismapped() and not a.preview_video.winfo_ismapped())
    p2=str(Path(tmp)/'rev30.mp4')
    _vw=_cv2.VideoWriter(p2,_cv2.VideoWriter_fourcc(*'mp4v'),30,(96,64))
    for i in range(60):_vw.write(np.zeros((64,96,3),np.uint8))
    _vw.release()
    with mock.patch.object(messagebox,'showwarning') as sw:a.set_video(p2)
    check("fps mismatch warns", sw.called)
    a.set_video(p,quiet=True)
    # A/B loop wraps during review playback (not while recording).
    # NOTE: check frame before root.update() — update() would keep
    # dispatching the live after(20,tick) chain and never return.
    a.frame=0;a.loop_a=5;a.loop_b=8;a.loop_on.set(True)
    a.running=True;a.recording=False;a.clock=_time.monotonic()-0.6
    a.tick()
    check("loop wraps", a.frame==5)
    a.running=False;a.loop_on.set(False);a.loop_a=a.loop_b=None
    root.update()
    # mode switch with no video prompts load; cancel falls back to wireframe
    a.clear_video()
    check("video cleared", a.video is None and a.preview_mode.get()=='wireframe')
    with mock.patch.object(filedialog,'askopenfilename',return_value=''):
        a.preview_mode.set('video');a.on_preview_mode();root.update()
    check("video mode without video falls back", a.preview_mode.get()=='wireframe')
    # project roundtrip keeps the video path and restores it
    a.set_video(p,quiet=True)
    pp=str(Path(tmp)/'v.dms.json')
    with mock.patch.object(filedialog,'asksaveasfilename',return_value=pp):
        a.save_project()
    check("video path saved", json.loads(Path(pp).read_text())['video_path']==p)
    a.clear_video()
    with mock.patch.object(filedialog,'askopenfilename',return_value=pp):
        a.open_project()
    check("video restored on open", a.video is not None and a.video_path==p)
    a.clear_video()

a.close()
print()
print("FAILURES:", failures if failures else "none")
sys.exit(1 if failures else 0)

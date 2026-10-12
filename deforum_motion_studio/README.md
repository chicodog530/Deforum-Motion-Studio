# Deforum Motion Studio 0.3

A standalone Windows 11 desktop app for creating 3D Deforum camera schedules.
Includes your supplied successful configuration as the starting baseline.

## What's new in 0.3

- **Sound channels**: the old single bass pulse is now a channel list. Each
  channel watches its own frequency band (bass, snare, crash, ...) and drives
  its own motion axis with its own pulse shape, threshold, timing and
  start/end range. Channels may overlap; overlapping pulses on one axis add.
  Ships with Bass / Snare / Crash presets (snare and crash disabled by default).
- **Smaller exports**: the exporter now writes error-bounded keyframes instead
  of one keyframe per frame (max deviation 1e-4, far below visible).
- **Dark mode**: toggle in the top bar, remembered between sessions.
- **Playback robustness**: control changes are coalesced to one rebuild per UI
  tick and the preview catch-up chain is guarded, so adjusting settings or
  hitting rewind mid-playback no longer freezes the UI.
- Project files are now version 2; v0.2 projects open with the old bass pulse
  migrated into a single Bass channel.

## Install and launch

1. Extract the whole ZIP to a normal folder (do not run inside the ZIP).
2. Install 64-bit Python **3.12 or 3.11** from https://www.python.org/downloads/windows/ if needed. Include the Python launcher.
3. Double-click **install.bat**. It downloads dependencies into a separate `.venv` inside this folder. It does not install anything into Forge's environment.
4. Double-click **run.bat**.

Internet is needed during installation. The app processes motion and music locally.
Keep all the files together. Python 3.10 is also supported by the installer.

## First short test

1. The app opens the supplied baseline automatically. Its missing roll-schedule comma is repaired in memory. The original file remains unchanged.
2. In Presets, choose **Wave**, strength **0.25**, cycle **8 seconds**, fade **1 second**; click **Apply preset**.
3. Click **Play** to see the calibrated flat-depth camera preview. Load a picture if preferred.
4. Click **Export to Deforum** and save a new settings `.txt`.
5. In Forge's Deforum tab, use **Load All Settings** to load the exported file using your usual settings-file control.
6. Render a short section first. Judge actual smearing and adjust the limits/strength before rendering a whole soundtrack.

For a quick music test, load **examples/demo_bass.wav** in the Bass pulse tab. This is an included synthetic bass-hit track, not copyrighted music.

## Render / refine loop

Render the video in Deforum, then load it back with **Load rendered video** and pick a view: **Wireframe**, **Video**, or **Split** (both side by side). The video panel shows the exact rendered frame for the current timeline frame — video and motion curves stay in lockstep, and scrubbing the graph scrubs the picture too. Watch it, find the weak spots, then punch in fixes: seek to the section, set an **A/B loop** around it (Set A / Set B, enable Loop A-B) to repeat it, hit Record and perform the new move while watching the render. Checked axes replace only the recorded range, so the rest of your motion is untouched. Export again and re-render: the export carries your original settings including the seed, so the imagery stays put and only the motion changes. The video path is saved in the project (the video file itself is not embedded — keep it next to the project). Picture only; the app keeps playing your loaded soundtrack for audio.

The preview now reproduces the **flat-depth geometric warp** in the `animation.py` you supplied: translation scale 1/200, XYZ rotation order, FOV/aspect schedules, projection offsets, pixel-center conventions, and repeated frame-by-frame image warping. It uses the actual configured video resolution, then fits the result into the preview panel while preserving the video's proportions. Frame 0 is the initial image; movement starts at frame 1. There is no arbitrary preview magnification.

OpenCV performs sampling rather than Torch: its interpolation tables round coordinates to 1/32 pixel, so pixel values can differ slightly, especially after repeated warps. This preview is calibrated geometry, not a bit-identical rendering of Torch's sampler or an AI-generated video. AI regeneration, changing depth maps, cadence blending, noise, color changes and hybrid optical flow are not simulated. The preview refuses to show a calibrated prediction with depth warping, perspective flip or Shakify enabled. Your supplied baseline disables these features. Keep cadence 1 for the clearest initial render comparison.

FOV and aspect ratio are read from the loaded config. Change them in Forge and save/reload that config if needed. A loaded preview photo is resized to the configured video dimensions and warped as the starting frame. Strong push-and-return schedules can cause residual geometric drift because the image-warp operations are not exact inverses; the preview intentionally shows this instead of resetting the image artificially.

## Presets and continuous travel

Built-ins: Still, Gentle drift, Wave, Spiral, Figure eight, Rocking, Push in and Pull out,
plus two music-video moves: **Reveal drift** (starts close, pulls back to reveal while drifting
sideways, pushes back in) and **Fly-through** (steady forward push with a continuous lateral weave).
With **Auto-add Bass pulse + Mid ripple channels** checked, applying Reveal drift or Fly-through
also sets up a Bass pulse channel (35–180 Hz → Travel) and a Mid Ripple channel (1–4 kHz → Travel):
each detected mid-range hit launches a decaying waterdrop-style wobble on the zoom.

Set strength, cycle duration, fade time and start/end seconds. End **0** means the full video. **Replace** replaces motion and clears the controller take in that range; **Add** layers the preset over existing motion. Clicking Apply is required. Numerical spinboxes also accept typed values.

Continuous **Travel** is added to the current Z schedule, including presets and recordings. To start with only constant travel, apply Still first, then set Travel. Positive/negative values select opposite directions. Click Play or Update pulse to recompute typed controls.

Save/load custom presets as `.preset.json`. Custom presets contain the base motion plus controller take; sound channels and the additional Travel control remain project-level effects.

## Sound channels

1. In Sound channels, load MP3, WAV, FLAC, OGG or M4A.
2. Pick a channel in the list (Bass, Snare, Crash, or Add your own). Each
   channel has its own frequency band, detection mode, target axis, strength,
   attack/return, threshold, timing offset and start/end range.
3. Click **Re-analyze channels** (or **Sample soundtrack** to play the song
   while it analyzes — the original bass-pulse workflow). Audio is decoded
   once at 22.05 kHz and each enabled channel gets its own band envelope, so
   snare crack (~1.5–6 kHz) and crash wash (~6–11 kHz) are detectable
   alongside bass.
4. Choose **Beat hits** (detects peaks, with adjustable min gap between hits),
   **Bass intensity** (continuous energy follower), or **Ripple** (each hit
   launches a decaying waterdrop-style oscillation — great for mid-range
   hits rippling the zoom). Enable the channel to compose it with your movement.
5. **Start/end** pick where the effect applies; end 0 means the full video.
   Channels may overlap freely — pulses on the same axis add together, with a
   short taper at range edges so channels never click the motion.
6. Changing a channel's **low/high cutoff** automatically re-analyzes that
   channel from the cached audio (no need to re-decode); its old envelope is
   dropped the moment the band changes, so what you see is always what the
   channel is actually listening to.

Each pulse returns to the underlying moving path mathematically rather than
just setting speed to zero, and respects the per-axis speed limits: if headroom
is low the pulse is scaled uniformly to preserve its push/return balance. The
status bar shows the retained percentage. Overlapping channels each report
their own scale; the worst case is shown.

The soundtrack is analyzed up to the project's duration. Re-analyze after
extending duration. Full-song audio decoding is not repeated for every
parameter tweak. Optional playback uses pygame: seeked playback supports
MP3/OGG; start at frame 0 for other formats. Preview/audio alignment is
approximate; exported schedules use frame time, independent of render speed.

**Attach soundtrack path to exported config** optionally sets Deforum's
soundtrack fields. The audio file must remain accessible at that path on the
rendering PC. Otherwise add the same track afterward in your video editor,
starting at the matching time.

**Export safety:** if the soundtrack is loaded but an enabled channel was
never analyzed (e.g. you enabled it after analyzing, or the analysis was
still running), the app warns you before exporting instead of silently
writing a file with no pulse effects.

## Spectrum view

To the right of the motion graph, the spectrum analyzer shows the soundtrack's
peak-hold spectrum on a log frequency axis (0 dB = loudest), drawn vertically:
the top is the highest frequency, the bottom is bass. Each enabled channel's
frequency band is shaded in its color as a horizontal span, with a marker line
at the actual detected peak frequency inside that band — so you can see at a
glance whether a channel is really sitting on bass, mids or highs.

Click any peak in the spectrum to read its exact frequency, bass/mid/high
class and level. Double-click (or the **New channel at cursor** button) to
create a channel with its band centered on that peak — then pick its target
axis in the channel editor and Re-analyze. The spectrum is saved with the
project, so it survives reloads without the audio file.

## Xbox controller and mouse recording

Native Windows XInput is used. Connect an Xbox-compatible controller via USB or Bluetooth before recording. The connection status updates automatically. Controllers requiring a different API are not supported in this initial version.

The **Gamepad** tab is the controller settings and test page. Every stick, trigger and button is assignable: sticks and triggers drive any of the six motion axes, buttons trigger Record, Stop, Undo or Play — or drive an axis while held, like the bumpers do for roll. Each stick/trigger/button has its own **Max** — the movement per second at full deflection — plus an invert toggle, so e.g. the left stick can be gentle on sideways movement while the triggers stay aggressive on travel. The right-hand column shows live values as you wiggle each control, so you can test the mapping before recording. The mapping is saved automatically per user (settings.json) and is independent of projects.

The factory layout (also the Reset to defaults result):

| Input | Movement |
| --- | --- |
| Left stick | Sideways / vertical |
| Right stick | Turn / tilt |
| Right / left trigger | Forward / backward |
| Right / left bumper (hold) | Positive / negative roll |
| A | Start recording |
| B | Stop |
| X when stopped | Undo |

Stick dead zone and smoothing keep the useful range fine-grained; sensitivity now applies to the mouse sliders only, since each gamepad control carries its own max. **Stick response** sets the stick curve: 1 is linear past the dead zone (full stick always reaches the control's max), higher values soften the mid-range for finer small moves. Mouse sliders provide the same movement when there is no controller. Sliders stay where you place them; click Center sliders to stop their input. Controller release approaches zero through smoothing. Recording captures a playback-time motion schedule; it does **not** steer Forge live during rendering.

Seek to a frame and click Record. Checked axes replace the previous controller take in that recorded range; unchecked axes preserve it. The take is an additional layer over the base schedule. Stop adds a brief deceleration tail where space remains. Undo restores the previous motion/controls; Clear recording removes only the take. Save project before major changes. Undo is limited to 15 snapshots and does not undo soundtrack replacement.

## Limits and timing

Defaults: ±0.3 for lateral/vertical translation and rotations; ±1.75 for Z travel. Each axis has its own adjustable limit, including Z for stronger pulses. Imported baseline rotations up to ±0.5 are retained in the editable source but limited to the current output limit. Raise the relevant limit to reproduce them. These are user-tuned starting limits, not a guarantee against smearing.

Apply timing changes retimes existing motion approximately to preserve per-second movement. New time beyond the existing motion becomes still. Exports write reduced keyframes (linear interpolation reproduces the motion within 1e-4), so files stay small even for long videos. The initial version supports 2–30,000 frames and 1–120 FPS.

The supplied baseline is 800 frames, 24 FPS, 768×1120, 3D, cadence 3, depth warping disabled. Other rendering settings remain unchanged unless you deliberately change timing or attach a soundtrack. Depth/FOV/cadence affect the final response; very short pulses may not look as expected with cadence 3. Consider testing a copy at cadence 1 in Forge to compare; the app does not automatically change it.

## Schedules and compatibility

Edit the six base motion schedules in the Schedules tab and click Apply schedules. Numeric keyframes interpolate, including towards keys after the video end. Imported numeric schedules are expanded internally and exports contain an explicit value for each frame.

Expression support is intentionally limited: t (frame number), pi, arithmetic, sin, cos, tan, abs, sqrt, exp and log. Expressions are evaluated without Python eval. Unsupported expressions are rejected. Mixed numeric/expression schedules use active-segment evaluation; advanced Deforum/Parseq expression features are not reproduced. Other settings' expressions are preserved verbatim.

Configs with a nonempty Parseq manifest are blocked at export because Parseq may override the generated motion. This version supports the common six 3D motion fields and settings-file workflow; compatibility with your exact Forge fork needs a local render test. No Forge installation or extension files are modified.

## Saving and backup

Save project creates a `.dms.json` file with your original config, editable base motion, controller take, settings and per-channel audio analysis. Open project restores it. Your soundtrack and preview picture are not embedded. Keep the soundtrack and project together in your backups; reload the soundtrack if its path changes. Back up custom `.preset.json` files too.

## Validation and limitations

Run `python -m unittest -v test_motion.py test_geometry.py` to check baseline import, repairs, expression validation, future-keyframe interpolation, presets, keyframe reduction, multi-channel pulse combine and windowing, channel validation, audio band filtering, pulse return, headroom limits, retiming, preservation of non-motion settings, and calibrated geometry/projection/sampling conventions.

Development checks validate the motion engine and exports. A desktop GUI smoke test also checks launch, preset/undo, mouse recording, project save/open, export, soundtrack analysis/playback and rendering of all four tabs. Native Xbox input and final AI render quality require testing on your Windows machine. This is version 0.3.

References:
- https://github.com/deforum/sd-webui-deforum/wiki/Animation-Settings
- https://github.com/rewbs/sd-parseq (audio-driven parameters and relative motion)
- https://learn.microsoft.com/en-us/windows/win32/xinput/getting-started-with-xinput
- https://docs.scipy.org/doc/scipy/reference/signal.html
- https://www.pygame.org/docs/ref/music.html

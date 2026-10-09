# Deforum Motion Studio 0.2

A standalone Windows 11 desktop app for creating 3D Deforum camera schedules.
Includes your supplied successful configuration as the starting baseline.

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

The preview now reproduces the **flat-depth geometric warp** in the `animation.py` you supplied: translation scale 1/200, XYZ rotation order, FOV/aspect schedules, projection offsets, pixel-center conventions, and repeated frame-by-frame image warping. It uses the actual configured video resolution, then fits the result into the preview panel while preserving the video's proportions. Frame 0 is the initial image; movement starts at frame 1. There is no arbitrary preview magnification.

OpenCV performs sampling rather than Torch: its interpolation tables round coordinates to 1/32 pixel, so pixel values can differ slightly, especially after repeated warps. This preview is calibrated geometry, not a bit-identical rendering of Torch's sampler or an AI-generated video. AI regeneration, changing depth maps, cadence blending, noise, color changes and hybrid optical flow are not simulated. The preview refuses to show a calibrated prediction with depth warping, perspective flip or Shakify enabled. Your supplied baseline disables these features. Keep cadence 1 for the clearest initial render comparison.

FOV and aspect ratio are read from the loaded config. Change them in Forge and save/reload that config if needed. A loaded preview photo is resized to the configured video dimensions and warped as the starting frame. Strong push-and-return schedules can cause residual geometric drift because the image-warp operations are not exact inverses; the preview intentionally shows this instead of resetting the image artificially.

## Presets and continuous travel

Built-ins: Still, Gentle drift, Wave, Spiral, Figure eight, Rocking, Push in and Pull out.

Set strength, cycle duration, fade time and start/end seconds. End **0** means the full video. **Replace** replaces motion and clears the controller take in that range; **Add** layers the preset over existing motion. Clicking Apply is required. Numerical spinboxes also accept typed values.

Continuous **Travel** is added to the current Z schedule, including presets and recordings. To start with only constant travel, apply Still first, then set Travel. Positive/negative values select opposite directions. Click Play or Update pulse to recompute typed controls.

Save/load custom presets as `.preset.json`. Custom presets contain the base motion plus controller take; bass pulses and the additional Travel control remain project-level effects.

## Music and bass pulse

1. In Bass pulse, load MP3, WAV, FLAC, OGG or M4A.
2. The app filters the audio to **35–180 Hz** by default. Change the band and click Re-analyze bass if desired.
3. Choose Beat hits or Bass intensity. Enable camera pulse to compose it with your movement.
4. Start with pulse distance **2**, attack **0.08 s**, return **0.22 s**. Adjust while previewing.
5. Threshold controls detection of bass peaks. A higher value selects louder peaks. Positive timing offset delays the visual pulse. Beat hits are detected bass peaks, not guaranteed musical beat/downbeat recognition; sustained bass may work better with Bass intensity.

Pulse distance is a relative position offset, not meters. The engine converts this offset into per-frame Z increments. It returns to the underlying moving path mathematically, rather than just setting Z speed to zero. Image warping/regeneration is not perfectly reversible, so actual pictures can retain artifacts.

The combined movement respects the per-axis speed limits. If there is insufficient Z headroom, the **whole pulse** is reduced uniformly to preserve its push/return balance. The status bar shows the retained percentage. Increase the Travel/distance limit in Controller if you need stronger bass movement; begin with short render tests. Base/recording motion exceeding the limits is clipped and reported.

The soundtrack is analyzed up to the project's duration. Re-analyze after extending duration. Full-song audio decoding is not repeated for every parameter tweak. Optional playback uses pygame: seeked playback supports MP3/OGG; start at frame 0 for other formats. Preview/audio alignment is approximate; exported schedules use frame time, independent of render speed.

**Attach soundtrack path to exported config** optionally sets Deforum's soundtrack fields. The audio file must remain accessible at that path on the rendering PC. Otherwise add the same track afterward in your video editor, starting at the matching time.

## Xbox controller and mouse recording

Native Windows XInput is used. Connect an Xbox-compatible controller via USB or Bluetooth before recording. The connection status updates automatically. Controllers requiring a different API are not supported in this initial version.

| Input | Movement |
| --- | --- |
| Left stick | Sideways / vertical |
| Right stick | Turn / tilt |
| Right / left trigger | Forward / backward |
| Right / left bumper | Positive / negative roll |
| A | Start recording |
| B | Stop |
| X when stopped | Undo |

Sensitivity, dead zone and smoothing keep the useful range fine-grained. Mouse sliders provide the same movement when there is no controller. Sliders stay where you place them; click Center sliders to stop their input. Controller release approaches zero through smoothing. Recording captures a playback-time motion schedule; it does **not** steer Forge live during rendering.

Seek to a frame and click Record. Checked axes replace the previous controller take in that recorded range; unchecked axes preserve it. The take is an additional layer over the base schedule. Stop adds a brief deceleration tail where space remains. Undo restores the previous motion/controls; Clear recording removes only the take. Save project before major changes. Undo is limited to 15 snapshots and does not undo soundtrack replacement.

## Limits and timing

Defaults: ±0.3 for lateral/vertical translation and rotations; ±1.75 for Z travel. Each axis has its own adjustable limit, including Z for stronger pulses. Imported baseline rotations up to ±0.5 are retained in the editable source but limited to the current output limit. Raise the relevant limit to reproduce them. These are user-tuned starting limits, not a guarantee against smearing.

Apply timing changes retimes existing motion approximately to preserve per-second movement. New time beyond the existing motion becomes still. Exact integer-frame schedules are exported, so long videos produce larger settings files. The initial version supports 2–30,000 frames and 1–120 FPS.

The supplied baseline is 800 frames, 24 FPS, 768×1120, 3D, cadence 3, depth warping disabled. Other rendering settings remain unchanged unless you deliberately change timing or attach a soundtrack. Depth/FOV/cadence affect the final response; very short pulses may not look as expected with cadence 3. Consider testing a copy at cadence 1 in Forge to compare; the app does not automatically change it.

## Schedules and compatibility

Edit the six base motion schedules in the Schedules tab and click Apply schedules. Numeric keyframes interpolate, including towards keys after the video end. Imported numeric schedules are expanded internally and exports contain an explicit value for each frame.

Expression support is intentionally limited: t (frame number), pi, arithmetic, sin, cos, tan, abs, sqrt, exp and log. Expressions are evaluated without Python eval. Unsupported expressions are rejected. Mixed numeric/expression schedules use active-segment evaluation; advanced Deforum/Parseq expression features are not reproduced. Other settings' expressions are preserved verbatim.

Configs with a nonempty Parseq manifest are blocked at export because Parseq may override the generated motion. This version supports the common six 3D motion fields and settings-file workflow; compatibility with your exact Forge fork needs a local render test. No Forge installation or extension files are modified.

## Saving and backup

Save project creates a `.dms.json` file with your original config, editable base motion, controller take, settings and bass analysis. Open project restores it. Your soundtrack and preview picture are not embedded. Keep the soundtrack and project together in your backups; reload the soundtrack if its path changes. Back up custom `.preset.json` files too.

## Validation and limitations

Run `python -m unittest -v test_motion.py test_geometry.py` to check baseline import, repairs, expression validation, future-keyframe interpolation, presets, audio band filtering, pulse return, headroom limits, retiming, preservation of non-motion settings, and calibrated geometry/projection/sampling conventions.

Development checks validate the motion engine and exports. A desktop GUI smoke test also checks launch, preset/undo, mouse recording, project save/open, export, soundtrack analysis/playback and rendering of all four tabs. Native Xbox input and final AI render quality require testing on your Windows machine. This is version 0.2, intended for that first practical test.

References:
- https://github.com/deforum/sd-webui-deforum/wiki/Animation-Settings
- https://github.com/rewbs/sd-parseq (audio-driven parameters and relative motion)
- https://learn.microsoft.com/en-us/windows/win32/xinput/getting-started-with-xinput
- https://docs.scipy.org/doc/scipy/reference/signal.html
- https://www.pygame.org/docs/ref/music.html

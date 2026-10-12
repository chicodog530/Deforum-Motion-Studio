# v0.2 validation

- 20 automated motion/geometry tests passed.
- GUI launch, calibrated pulse preview, mouse recording, undo, project save/open and export checks passed.
- Compared one-frame warps directly against the supplied animation.py running on CPU Torch, using Deforum's standard py3d_tools camera helper.
- Sample: 128 x 192 RGB reference grid; FOV 70, aspect 1, depth disabled, bicubic sampling, border padding.
- Mean absolute pixel difference: +5 Z = 0.429; mixed six-axis move = 0.388; -5 Z = 0.415 (RGB levels out of 255).
- Camera coordinate/projection tests pass independently. OpenCV sampler quantization explains the small pixel differences; repeated sampling can accumulate them.
- This validates flat-depth image geometry. AI regeneration, cadence blending, Shakify, depth estimation and final Forge video appearance are not reproduced.
- No arbitrary preview magnification is applied. Native configured dimensions and portrait proportions are retained.

# v0.3 validation

- 26 automated motion/geometry tests passed (18 motion incl. keyframe
  reduction, multi-channel combine/windowing/validation; 8 geometry).
- Headless GUI smoke test (28 checks) passed under Xvfb: launch, default
  channels, channel add/duplicate/remove, per-channel editing with debounced
  rebuild, dark/light theme toggle with settings persistence, project v2
  save/open, v0.2 project migration (bass pulse -> Bass channel), undo of
  channel edits, export->reload roundtrip (max error 4.74e-9 on the baseline;
  37 keyframes vs 4800 previously), play/stop/rewind, baseline repair import.
- Export keyframe bound verified: worst-case deviation 1e-4 in Deforum units
  (~0.0002 px translation, 1e-4 deg rotation), far below the preview's own
  +-1/32 px OpenCV sampler quantization.
- Native Xbox input, real soundtrack analysis and final AI render quality
  still require testing on the Windows machine.

# v0.3.1 validation (spectrum view)

- spectrum_db unit test: 440 Hz tone peaks within one log bin, 0 dB normalized.
- Headless GUI smoke test additions (6 checks): spectrum click sets cursor
  frequency and readout, double-click/button creates a channel with its band
  bracketing the cursor, canvas redraws with cursor marker.
- freq_class unit test: 60/249 Hz bass, 250/1000 Hz mid, 2000/5000 Hz high.

# v0.3.2 validation (band-change auto re-analysis)

- Root-caused a stale-envelope bug from a user screenshot: editing a
  channel's low/high cutoff never re-analyzed, so the channel kept the old
  band's envelope while displaying the new band. Fix: band edits clear the
  envelope immediately and recompute it from cached decoded audio (debounced
  800 ms while typing); full re-decode only happens on Re-analyze channels.
- Headless GUI smoke test additions (8 checks): full mp3 analysis end to end,
  audio cache, transient invalid band ignored, stale envelope cleared on band
  change, re-analysis completes with new band recorded and a genuinely
  different envelope.

# v0.3.3 validation (vertical spectrum)

- Spectrum moved from a horizontal strip to a vertical analyzer on the right
  of the motion graph: top = highest frequency, bottom = bass. Channel bands
  are horizontal shaded spans with peak-frequency marker lines.
- Headless GUI smoke checks: click near top yields >2 kHz, near bottom
  yields <250 Hz. Canvas geometry verified numerically: 10k grid label above
  50 Hz, bass band rect below snare band rect, peak markers inside their
  channel bands.

# v0.3.4 validation (pulse export safety + sample soundtrack)

- Lean data-path repro (no GUI): decode 30s mp3 at 22050 Hz -> bass envelope
  -> channel_pulse -> combine_multi with recorded X motion -> export ->
  parse back. File Z max 1.75 (714 samples |z|>0.2), matches app output
  exactly: the pulse reaches the file whenever analysis completed.
- Root cause of "render has xbox motion but no pulse": the app silently
  exported when an enabled channel had no envelope (e.g. enabled after
  analyzing, or analysis still running). Fix: export now warns and names the
  un-analyzed channels, with Export anyway / cancel.
- Restored the original "Sample soundtrack" button (play + analyze).
- Fixed decode_audio's default sample rate (8000 -> CHANNEL_SR); the app
  always passed 22050 explicitly, but the default was a footgun.
- Smoke +4: decode default, export asks on missing pulse, cancel skips file
  dialog, export-anyway writes, sample triggers analysis + playback.

# v0.3.5 validation (TikTok-style music presets + Ripple mode)

- Watched the user's two TikTok clips frame by frame: both are pure
  translation moves — slow Z push/pull reveals with lateral drift (skulls),
  steady forward push with a continuous weave (city). Built matching
  presets: 'Reveal drift' (Z in/out + X drift) and 'Fly-through'
  (constant Z push + X weave + Y bob).
- New 'Ripple' channel mode: each detected hit launches a damped cosine
  (~1 wobble per release time constant, ~3-4 visible rings), verified to
  oscillate both ways and decay.
- Presets tab checkbox "Auto-add Bass pulse + Mid ripple channels": applying
  Reveal drift / Fly-through configures Bass (35-180 Hz, Beat hits, Travel)
  and creates a Mid Ripple channel (1-4 kHz, Ripple, Travel); re-running
  never duplicates the ripple channel. End-to-end GUI check passes.

# Gamepad settings/test page (post-v0.3)

- New Gamepad tab: every stick, trigger and button assignable. Sticks/triggers
  drive any motion axis; buttons fire Record/Stop/Undo/Play or drive an axis
  while held (bumpers -> roll by default, preserving the classic layout).
- Per-control Max (movement/sec at full deflection) + invert toggle; mapping
  saved per user in settings.json, independent of projects.
- Live test readouts: wiggle the controller, values update on each row.
- 27 unit tests pass (5 new: read_raw keys, default map incl. bumpers,
  pad_axis_target incl. dead zone/invert/remap, button-held axis drive,
  validate_pad_map repair).
- Headless GUI smoke test: 72 checks pass (14 new: tab build, defaults,
  UI remap -> pad_map + settings.json persistence, button->axis remap,
  fake-controller live readouts, reset to defaults).

# Stick response fix (post-v0.3)

- Report: with smoothing at minimum, sticks still felt dampened/sluggish.
  Root causes were not smoothing (at 0.01 s it converges in ~3 ticks): the
  fixed 1.7-power response curve (half stick -> ~22% output) and default
  per-control max at half the axis limit.
- New Controller-tab "Stick response (1=linear)" slider, default 1.0: linear
  past the dead zone, higher values re-soften the mid-range.
- Default per-control max is now the full axis limit (was half); the
  per-axis speed limits still cap the combined total. Saved mappings keep
  the user's own values.
- 28 unit tests + 73 headless GUI checks pass.

# High-cutoff Nyquist fix (post-v0.3)

- Screenshot report: editing Crash to 8800/11050 Hz popped "low cutoff must
  be below high cutoff (under 11025 Hz)" even though low < high. Root cause:
  the message blamed the wrong bound — high=11050 exceeded the Nyquist
  limit (11025 Hz at the 22050 Hz analysis rate), which the UI spinbox
  allowed (max 12000) and validate_channel allowed (clamped to 22050).
- Fix: validate_channel now clamps high to 11024 (strictly under Nyquist,
  which the bandpass filter requires); the channel editor spinbox max is
  11024; analyze_audio reports each violated bound accurately instead of
  one misleading message.
- Regression test uses the exact screenshot values (8800/11050): clamps
  cleanly and the envelope analyzes.
- 29 unit tests + 73 headless GUI checks pass.

# Render/refine loop (post-v0.3)

- New review-video workflow: Load rendered video -> Review video toggle shows
  the exact rendered frame for the current timeline frame (frame N of video
  for frame N of motion — no separate video clock, so scrubbing the motion
  graph scrubs the picture too). Punch in fixes while watching: seek to the
  weak section, set an A/B loop (Set A / Set B + Loop A-B) to repeat it,
  Record a new take; checked axes replace only the recorded range.
  Re-export carries the original settings incl. seed, so re-renders keep
  the imagery and change only the motion.
- video_review.py: cv2 frame-accurate reader, sequential fast path, seek via
  keyframe + forward decode, 16-frame LRU; panel-size-aware decode cache in
  draw_preview so repeated redraws don't re-decode.
- FPS/frame-count mismatches warn (1:1 index mapping). Video path saved in
  the project (file not embedded). Picture only — the loaded soundtrack
  remains the audio.
- 31 unit tests pass (3 new VideoReview: sequential/seek/clamp/bad path).
  82 headless GUI checks pass (12 new: load, view toggle, frame render,
  fps-mismatch warning, A/B wrap, toggle-off/cancel, clear, project
  save/open roundtrip with video restore).

# Split preview view (post-v0.3)

- Preview pane now has three modes: Wireframe, Video, Split (both side by
  side, each in its own canvas). Radio buttons replace the old toggle.
- Layout re-packs canvases only on mode change (guarded: re-packing fires
  <Configure>, which would otherwise recurse via draw_preview).
- Video and wireframe keep separate PhotoImage refs; the video decode cache
  is keyed by (frame, panel w, h) and invalidated on video switch/clear.
- 31 unit tests + 86 headless GUI checks pass (4 new split checks: canvas
  visibility per mode, both panels drawn in split).

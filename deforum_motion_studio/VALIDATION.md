# v0.2 validation

- 20 automated motion/geometry tests passed.
- GUI launch, calibrated pulse preview, mouse recording, undo, project save/open and export checks passed.
- Compared one-frame warps directly against the supplied animation.py running on CPU Torch, using Deforum's standard py3d_tools camera helper.
- Sample: 128 x 192 RGB reference grid; FOV 70, aspect 1, depth disabled, bicubic sampling, border padding.
- Mean absolute pixel difference: +5 Z = 0.429; mixed six-axis move = 0.388; -5 Z = 0.415 (RGB levels out of 255).
- Camera coordinate/projection tests pass independently. OpenCV sampler quantization explains the small pixel differences; repeated sampling can accumulate them.
- This validates flat-depth image geometry. AI regeneration, cadence blending, Shakify, depth estimation and final Forge video appearance are not reproduced.
- No arbitrary preview magnification is applied. Native configured dimensions and portrait proportions are retained.

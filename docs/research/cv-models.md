# CV Models — Gaze + Hand-Flick (MVP, OpenVINO-IR, NPU-first)

Research date 2026-06-15. Targets host: Lunar Lake, OpenVINO 2026.2, NPU "Intel AI
Boost". `[D]`=verified from docs/repo, `[H]`=needs HOST validation (NPU compile +
real-camera accuracy). NPU mandates **static shapes** `[D]` — all picks below are
static or reshaped-to-static once.

## Decision summary

- **GAZE primary**: OMZ classic 4-model pipeline (face-detect -> head-pose +
  landmarks -> gaze-estimation-adas-0002). Static, tiny, NPU-native, drop-in. Dwell
  focus needs only coarse direction, so 7° MAE is ample.
- **GAZE fallback**: L2CS-Net / MobileGaze (yakhyo, MobileOne backbone) ONNX ->
  OV. Single-model appearance gaze; use if OMZ head-pose+landmark chain proves
  brittle on this webcam.
- **HAND primary**: MediaPipe palm-detection + hand-landmark, **converted in-house
  from official `.tflite` via OpenVINO `convert_model`** (2026 native TFLite
  frontend — no Docker/PINTO legacy toolchain). Use landmark wrist/MCP centroid;
  track horizontal velocity -> flick with hysteresis.
- **HAND fallback**: palm-detection alone (bbox center) + centroid-velocity, skip
  landmark model. Or PINTO_model_zoo 033 pre-converted IR if our conversion fails
  NPU compile.

Key insight: OMZ is the only source giving ready static IR + permissive license +
trivial size. MediaPipe gives best hand quality but we own the conversion.

## A. GAZE — OMZ classic pipeline (PRIMARY)

4 sequential Intel models. All `framework: dldt` (already IR), Apache-2.0,
`storage.openvinotoolkit.org/.../2023.0/...` snapshot live `[D]`. FP16 ~half of
FP32 size; prefer **FP16** on NPU/GPU.

| Model | Input (name, shape, NCHW) | Output | Notes |
|---|---|---|---|
| face-detection-retail-0004 | `data` `[1,3,300,300]` BGR | `[1,1,200,7]` SSD `[image_id,label,conf,xmin,ymin,xmax,ymax]` | 0.588 GFLOPs, 1.07 MParams. -0005 is MobileNetV2 variant, input `input.1` same `[1,3,300,300]`, out `[1,1,200,7]`. `[D]` |
| head-pose-estimation-adas-0001 | `data` `[1,3,60,60]` BGR (face crop) | 3 blobs `angle_y_fc,angle_p_fc,angle_r_fc` each `[1,1]` (yaw,pitch,roll deg) | 0.105 GFLOPs, 1.91 MParams. `[D]` |
| facial-landmarks-35-adas-0002 | `data` `[1,3,60,60]` BGR (face crop) | `[1,70]` = 35 (x,y) normalized; eyes corners drive eye crops | 0.042 GFLOPs, 4.6 MParams. `[D]` |
| gaze-estimation-adas-0002 | `left_eye_image`,`right_eye_image` `[1,3,60,60]`; `head_pose_angles` `[1,3]` | `gaze_vector` `[1,3]` Cartesian, **not unit-length** | 0.139 GFLOPs, 1.882 MParams. MAE 6.95° (sd 3.58). `[D]` |

Pipeline glue (our code): SSD face box -> crop -> head-pose (3 angles) +
landmarks-35 -> use landmark eye-corner pts to cut two 60x60 eye crops -> feed
gaze model with `[yaw,pitch,roll]` -> 3D gaze vector. Reference flow: OMZ
`gaze_estimation_demo` `[D]`.

NPU feasibility `[H]`: all static, conv/FC-only, sub-1-GFLOP -> expected clean NPU
compile (NPU handles static vision models well `[D]`). Validate each `compile_model(m,"NPU")`
on host; fall back per-model to GPU then CPU if any op unsupported. face-detect SSD
DetectionOutput layer is the most likely NPU-unsupported op -> may pin face-detect
to GPU/CPU, keep the three 60x60 nets on NPU.

Why primary: zero conversion, static by construction, ~16 MB total FP16, license
clean, dwell-focus only needs the gaze direction sign/quadrant + head pose, all of
which this delivers. MAE ~7° over a 27" desktop ≈ a few-cm gaze cursor error —
adequate with dwell + window-snap.

### Gaze alternatives (evaluated, not chosen for MVP)
- **L2CS-Net / MobileGaze** (yakhyo/gaze-estimation, updated 2026-02 `[D]`):
  single ResNet/MobileNet/MobileOne net, face img -> (yaw,pitch). ONNX export +
  webcam scripts shipped; needs RetinaFace/uniface for face crop. OV path = ONNX ->
  `convert_model`, reshape face input to static. **Best fallback**: fewer moving
  parts than 4-net chain, SOTA 3.9° MPIIGaze. Cost: we convert + verify NPU.
- **MediaPipe FaceMesh + iris** (478 pts + iris): excellent eye landmarks but gaze
  is geometric (we derive vector) and conversion mirrors hand effort; heavier.
  Reserve only if eye-crop quality from landmarks-35 is too coarse.
- **OpenSeeFace**: ONNX face+landmark tracker, gaze via geometry; CPU-oriented,
  not a turnkey gaze vector. Skip for MVP.
- **Geometry-only (landmarks-35 + head-pose, no gaze net)**: viable ultra-light
  fallback if gaze-estimation-adas-0002 disappoints; lower accuracy.

## B. HAND — MediaPipe palm+landmark -> OV (PRIMARY)

For left/right **flick** we need hand presence + horizontal motion only. MediaPipe
2-stage is highest-quality; OMZ has **no** good hand-landmark model (gap) `[D]`.

Conversion path (2026, in-house) `[D]`:
1. Download official bundle `hand_landmarker.task` (a zip) and unzip -> contains
   `palm_detection_full.tflite` + `hand_landmark_full.tflite` (+ GHUM assets).
2. `ov.convert_model("palm_detection_full.tflite")` then `ov.save_model(...)`.
   OpenVINO 2026 reads `.tflite` natively (TFLite frontend; >80% MediaPipe/Kaggle
   TFLite supported) — no PINTO `tflite2tensorflow` Docker needed.
3. Bake preprocessing into our code (TFLite expects RGB; OpenCV gives BGR): reverse
   channels + scale. Palm expects RGB [-1,1] (mean/scale 127.5); landmark expects
   RGB [0,1] (scale 255) `[D, from geaxgx params]`.
4. Reshape to static once (`model.reshape({...})`) for NPU.

| Model | Input shape | Output (key) | Notes |
|---|---|---|---|
| palm_detection_full | `[1,192,192,3]` NHWC (full variant) | scores `[1,2016,1]` + boxes/keypoints `[1,2016,18]` -> NMS host-side to hand bbox + 7 kpts | 192x192 for *full*; *lite* uses 192 too. Anchor decode + NMS in our numpy. `[D]` |
| hand_landmark_full | `[1,224,224,3]` NHWC | 21 landmarks `[1,63]` (x,y,z norm), handedness `[1,1]`, presence `[1,1]` | 21x3 keypoints; wrist=idx0, MCPs=5,9,13,17. `[D]` |

Flick logic (our code, no extra model): per frame take hand-center =
mean(wrist, middle-MCP) in normalized x. Maintain short ring buffer; compute dx/dt.
Hysteresis: arm when |vx| > V_on AND hand present N frames; fire LEFT/RIGHT on
sign; disarm until |vx| < V_off and refractory ms elapsed. Tune V_on/V_off/refractory
on host. This rejects jitter and double-fires.

NPU feasibility `[H]`: reshape both to static NHWC. Palm-detect raw tensors are
clean conv; the *post-process* (anchor decode/NMS) stays on host (numpy) — do NOT
try to NPU-compile NMS. Landmark net is pure regression -> NPU-friendly. Verify
compile on host; GPU fallback likely fine if NPU rejects a TFLite-origin op
(Interpolate/ResizeBilinear conversions can differ slightly `[D]`).

### Hand alternatives
- **palm-detection only + bbox-centroid velocity** (FALLBACK): skip landmark
  model; track bbox center x. Lighter, slightly noisier center. Good enough for
  flick; fastest to ship.
- **PINTO_model_zoo `033_Hand_Detection_and_Tracking`** (MIT scripts; source-model
  license applies): pre-converted OV IR (128/192/256, FP32/FP16/INT8) via download
  scripts. Use if our `convert_model` output fails NPU compile. `[D]`
- **geaxgx/openvino_hand_tracker**: ships only `.blob` (Myriad X) built w/ OV
  2021.2 — **not** usable for NPU; reference for normalization params only. `[D]`
- **Generic lightweight detector + centroid**: any small person/hand detector ->
  centroid velocity. Over-engineering vs palm-detect for MVP.

## C. Onboarding / download plan

Script `scripts/fetch-models.sh` (or py): download -> sha256 verify -> place under
`models/` (git-ignored; large weights uncommittable). Pin URLs; record license per
model. **sha256 are placeholders — fill from first real download on host (`[H]`).**

OMZ gaze pipeline (Apache-2.0; LICENSE
https://raw.githubusercontent.com/openvinotoolkit/open_model_zoo/master/LICENSE):
prefer FP16. Pattern: `.../<NAME>/FP16/<NAME>.{xml,bin}`.

```
BASE=https://storage.openvinotoolkit.org/repositories/open_model_zoo/2023.0/models_bin/1
# face-detection-retail-0004  FP16  (~1.1 MB bin)
$BASE/face-detection-retail-0004/FP16/face-detection-retail-0004.xml   sha256:<TODO>
$BASE/face-detection-retail-0004/FP16/face-detection-retail-0004.bin   sha256:<TODO>
# head-pose-estimation-adas-0001 FP16 (~3.8 MB bin)
$BASE/head-pose-estimation-adas-0001/FP16/head-pose-estimation-adas-0001.xml sha256:<TODO>
$BASE/head-pose-estimation-adas-0001/FP16/head-pose-estimation-adas-0001.bin sha256:<TODO>
# facial-landmarks-35-adas-0002 FP16 (~9.2 MB bin)
$BASE/facial-landmarks-35-adas-0002/FP16/facial-landmarks-35-adas-0002.xml sha256:<TODO>
$BASE/facial-landmarks-35-adas-0002/FP16/facial-landmarks-35-adas-0002.bin sha256:<TODO>
# gaze-estimation-adas-0002 FP16 (~3.8 MB bin)
$BASE/gaze-estimation-adas-0002/FP16/gaze-estimation-adas-0002.xml     sha256:<TODO>
$BASE/gaze-estimation-adas-0002/FP16/gaze-estimation-adas-0002.bin     sha256:<TODO>
# (FP32 == swap FP16->FP32 in path, ~2x bin size)
```

MediaPipe hand bundle (Apache-2.0; models are Google MediaPipe assets):
```
# versioned (pin '1', not 'latest', for reproducible checksum)
https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task  sha256:<TODO>
# unzip -> palm_detection_full.tflite, hand_landmark_full.tflite -> ov.convert_model -> save IR under models/hand/
```

Gaze fallback (only if pursued): MobileGaze ONNX from yakhyo/gaze-estimation
releases (check repo LICENSE before redistribute) `sha256:<TODO>`; convert ONNX ->
IR.

Sizes (approx, FP32): face-det ~4.3MB, head-pose ~7.6MB, landmarks-35 ~18.4MB,
gaze ~7.5MB; FP16 ≈ half. MediaPipe palm ~2MB, landmark ~2MB (.tflite). Total
footprint well under typical LFS thresholds but still keep `models/` ignored.

## D. Verified vs HOST-validation

Verified from docs/repo `[D]`: model existence + live 2023.0 URLs, input/output
names+shapes, GFLOPs/MParams, Apache-2.0, OMZ "maintenance mode" (NOT archived),
OpenVINO 2026 native `.tflite` `convert_model`/`read_model`, NPU static-shape
requirement, MediaPipe 2-stage shapes (192/224, 21 kpts), MobileGaze maintained
2026-02.

Needs HOST validation `[H]`: (1) every `compile_model(...,"NPU")` actually
succeeds per model / which ops force GPU/CPU fallback; (2) real accuracy on THIS
UVC webcam + lighting (gaze MAE in practice, flick false-fire rate); (3) end-to-end
latency budget on NPU vs GPU; (4) all sha256 checksums; (5) MediaPipe TFLite ops
survive OV conversion + static reshape without NPU rejection.

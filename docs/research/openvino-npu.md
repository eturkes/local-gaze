# OpenVINO 2026.2 Runtime + NPU on Intel Lunar Lake (lane note)

Scope: which OpenVINO layer to use for gaze/hand CV, how to drive the NPU
(Intel AI Boost, NPU 3720) on this host, device selection + CPU fallback, async
realtime loop, NPU usability probe, numpy compat. Verified against docs.openvino.ai
2026 + master RST + PyPI metadata (June 2026). `[V]`=verified-from-docs,
`[A]`=assumed/inferred.

## TL;DR decisions

- Use **OpenVINO Runtime** (`openvino.Core` / `compile_model` / `infer_request`)
  for gaze + hand CV. **Not** OpenVINO GenAI. `[V]`
- NPU needs **static shapes, batch=1**: always `model.reshape(...)` to fully
  static before `compile_model(model,"NPU")`. `[V]`
- NPU compute precision is **FP16**; for best NPU perf ship **INT8** (U8) or
  FP16 IR. `[V]`
- Cache compiled blobs with **`core.set_property({"CACHE_DIR": <dir>})`** before
  compile → cuts first-inference latency on later runs. `[V]`
- Selection strategy: **NPU-first, configurable, fail-soft**. Try explicit
  `"NPU"`; on failure fall back `"GPU"`→`"CPU"`. Prefer this over `"AUTO:NPU,CPU"`
  so we control + log the device and never silently degrade. Hint = **LATENCY**.
- **Never** instantiate Core or probe NPU from inside the container; host-only.
- numpy: openvino 2026.2 pins `numpy<2.5.0,>=1.16.6` → **NumPy 2.x OK**. `[V]`

## 1. Runtime vs GenAI — Runtime is correct

| | OpenVINO **Runtime** | OpenVINO **GenAI** |
|---|---|---|
| API | `ov.Core`, `compile_model`, `InferRequest` | `LLMPipeline`,`VLMPipeline`,`WhisperPipeline`,`Text2ImagePipeline` |
| For | arbitrary ONNX/IR CNN, static-shape CV | autoregressive token gen (LLM/VLM), tokenizer+detokenizer+sampling loop |
| Latency | single tensor in→out, sub-ms feasible | per-token loop, KV-cache, ≥100ms |

GenAI is the **wrong tool** for gaze/hand: our models (face/iris landmark, palm
detect, hand landmark) are **single-shot regressors/detectors** — one image
tensor → one numpy result, no token loop, no tokenizer, no sampling. GenAI's
`generate()` abstraction adds prompt handling + a generation loop that has no
meaning here and forbids the per-frame latency budget. `[V]` (GenAI pipelines are
defined only for LLM/VLM/Whisper/Image-gen.)

**Clean future seam (do not build now):** a *scene-understanding* / NL-command
backend. `openvino_genai.VLMPipeline(model_dir, "NPU")` takes a text prompt + an
image `ov.Tensor` (shape `(1,H,W,3)` uint8) and returns text — e.g. "describe the
window the user is looking at". Add it later as an **optional, separate**
`VlmBackend` behind the same daemon interface boundary; it must NOT sit in the
per-frame gaze/gesture hot path. `[V]`
```python
import openvino_genai as ov_genai           # future seam only
pipe = ov_genai.VLMPipeline(model_dir, "NPU")     # VLM still static-shape on NPU
print(pipe.generate("Describe the image", images=img_tensor, max_new_tokens=64))
```
Note NPU VLM prompt cap incl. image tokens = 1024 by default. `[V]`

## 2. NPU specifics: static shape, precision, caching

**Static shape (hard requirement).** NPU "supports only models with static
shapes". Reshape the loaded model to fully-defined dims, batch=1, before compile.
`[V]`
```python
import openvino as ov
core = ov.Core()
model = core.read_model("hand_landmark.xml")     # or .onnx
# pin every dim; example NCHW 1x3x224x224 (use the model's real input name/shape)
model.reshape({model.input(0).any_name: ov.PartialShape([1, 3, 224, 224])})
compiled = core.compile_model(model, "NPU")
```
Better: bake static shapes at conversion (`ovc --input "[1,3,224,224]"` /
`optimum-cli`) so no runtime reshape needed. `[A: standard ovc flag]`

**Precision.** NPU plugin inference precisions: **F32, F16, U8** (INT8 / mixed
FP16-INT8); **hardware compute precision is FP16**. So: convert IR to FP16, and
for max NPU throughput quantize to INT8 (NNCF). Query actual support: `[V]`
```python
caps = core.get_property("NPU", "OPTIMIZATION_CAPABILITIES")   # e.g. ['FP16','INT8',...]
```
Lunar Lake (Series 2) NPU additionally supports NF4 (LLM-only; irrelevant to CV). `[V]`

**First-compile latency + blob cache.** First `compile_model` on NPU is slow
(compiler builds the blob). Two cache layers:
- **UMD caching** — on by default in the NPU driver; hashes the IR. `[V]`
- **OpenVINO `CACHE_DIR`** — device-neutral; *enabling it auto-bypasses UMD*.
  Subsequent compiles `import` the blob instead of recompiling. Set it **before**
  compile. `[V]`
```python
core.set_property({"CACHE_DIR": "/home/.../.cache/local-gaze/ov"})  # str literal works
compiled = core.compile_model(model, "NPU")          # 1st run builds blob, later runs import
loaded_from_cache = compiled.get_property("LOADED_FROM_CACHE")      # bool, verify hit  [A: std prop]
```
Explicit blob export/import also exists (`compiled.export_model(stream)` /
`core.import_model(stream,"NPU")`, or 2026 `ov::compiled_blob` Tensor hint), but
docs warn **blobs are dev-only, not for production** (format is version/platform
specific). Prefer `CACHE_DIR` over hand-rolled export. `[V]`

`turbo` is an NPU perf knob: `core.compile_model(model,"NPU",{"NPU_TURBO": True})`
(C++ `ov::intel_npu::turbo(true)`). `[V, name=A]`

## 3. Device selection + CPU fallback (recommended strategy)

Default AUTO **excludes NPU** from its candidate list — NPU must be named
explicitly. AUTO's default hint is **LATENCY**; AUTO also runs the *first*
inference on CPU while the accelerator compiles
(`ENABLE_STARTUP_FALLBACK`, default true). `[V]`

For local-gaze prefer **explicit device with try/except**, not AUTO, so we log
the chosen device, fail-soft, and guarantee no container NPU claim:
```python
import openvino as ov
def pick_compiled(core, model, prefer=("NPU","GPU","CPU")):
    avail = core.available_devices                      # e.g. ['CPU','GPU','NPU']
    for dev in prefer:
        if dev not in avail:
            continue
        try:
            cfg = {"PERFORMANCE_HINT": "LATENCY"}        # str enum accepted
            return core.compile_model(model, dev, cfg), dev
        except Exception as e:
            log.warning("compile on %s failed: %s", dev, e)
    raise RuntimeError("no usable OpenVINO device")
```
- `PERFORMANCE_HINT` values: `"LATENCY"` (our default — single in-flight request,
  lowest per-frame time), `"THROUGHPUT"`, `"CUMULATIVE_THROUGHPUT"`. `[V]`
- AUTO alternative if ever wanted: `core.compile_model(model,"AUTO:NPU,CPU",
  {"PERFORMANCE_HINT":"LATENCY"})` (CPU absorbs first-frame latency, then NPU). `[V]`
- Device identity for logs/probe: `core.get_property("NPU","FULL_DEVICE_NAME")`
  → "Intel(R) AI Boost"; also `"DEVICE_ARCHITECTURE"`, `"SUPPORTED_PROPERTIES"`. `[V]`

## 4. Async inference for the realtime camera loop

For a camera loop, overlap capture/preprocess with inference using
`AsyncInferQueue`. With LATENCY hint keep the pool small (jobs=1–2) so we stay
latency-bound, not throughput-bound. `[V]`
```python
import openvino as ov
q = ov.AsyncInferQueue(compiled, 2)         # jobs=0 → auto-optimal; 2 = pipeline depth
def on_done(req, frame):                     # callback: (InferRequest, userdata); keep light
    out = req.get_output_tensor(0).data       # numpy view; copy if kept past callback
    handle(out, frame)
q.set_callback(on_done)
# per captured frame:
q.start_async({0: blob}, userdata=frame)     # blob: np.ndarray matching static input
# on shutdown: q.wait_all()
```
- `start_async(inputs=None, userdata=None, share_inputs=False)`; inputs dict keys
  int|str|ConstOutput, values numpy.ndarray. `[V]`
- Callbacks run on dedicated threads w/ GIL held → **no I/O / blocking** inside;
  copy `.data` (it's a view) before async reuse. `[V]`
- Mutating a single request pulled via `q[i]` invalidates the queue; only call
  `start_async`/`set_callback` on the queue object. `[V]`
- Synchronous alternative for the simplest path: one `ireq=compiled.create_infer_request();
  ireq.infer({0: blob}); out=ireq.get_output_tensor(0).data`. LATENCY hint pairs
  naturally with sync single-request. `[V]`
- **Import cost**: `import openvino` + first `ov.Core()` + first NPU compile are
  heavy (100s ms–seconds). **Lazy-import** openvino inside the daemon backend
  (never at container module import), construct Core once, warm up NPU at startup. `[A]`

## 5. NPU usability probe (beyond mere listing)

Listing `"NPU"` in `available_devices` ≠ usable. Probe must **compile a tiny
static model on NPU** and run one inference. Driver prereqs (all present on this
host): `intel_vpu` kernel module, `/dev/accel/accel0`, Intel **linux-npu-driver**
(UMD) + **level-zero** loader, kernel ≥6.6. `[V]`
```python
import numpy as np, openvino as ov
def npu_ok(core):
    if "NPU" not in core.available_devices:
        return False, "NPU not listed"
    try:
        n = ov.opset13.relu(ov.opset13.parameter([1,8], ov.Type.f32))   # tiny static graph
        m = ov.Model([n], [n.inputs()[0].get_node()], "probe")          # 1-in 1-out
        c = core.compile_model(m, "NPU")
        c.create_infer_request().infer({0: np.zeros((1,8), np.float32)})
        return True, core.get_property("NPU","FULL_DEVICE_NAME")
    except Exception as e:
        return False, repr(e)
```
(If building the Model node-graph is fiddly, ship a 1-node `probe.xml` IR in the
repo and `read_model` it instead — same intent: compile+infer on NPU.) `[A]`
Run this only from `scripts/host-probe` on the **host**; container must report
"unverified", never False-positive. `[V env-fact]`

## 6. numpy compatibility (venv shadowing)

- System `openvino` 2026.2 requires `numpy<2.5.0,>=1.16.6` → built for/works with
  **NumPy 2.x** (and 1.26.x). `[V PyPI]`
- Host plan = venv with `--system-site-packages` to inherit system `openvino`. `[V env-fact]`
- **Pitfall**: a venv that pip-installs *its own* numpy **shadows** the system
  numpy `openvino.so` was compiled against → possible ABI/`_ARRAY_API` errors. `[A]`
- **Mitigation**: do **not** pin/install numpy in the venv unless required;
  inherit the system one. If a dep forces a numpy install, keep it inside
  `>=1.16.6,<2.5.0` and verify post-install:
```python
import numpy, openvino as ov
print(numpy.__version__, ov.__version__, ov.Core().available_devices)   # must list NPU on host
```
- openvino 2026.2 wheels exist for cp312/cp313/cp314 (incl. free-threaded
  `cp314t`); host is py3.13 → matches the system pkg. `[V]`

## API quick-reference (Python, openvino 2026)

- Modern top-level import is **`openvino`** (`ov.Core`, `ov.AsyncInferQueue`,
  `ov.Tensor`, `ov.PartialShape`, `ov.properties`). `openvino.runtime.*` is the
  legacy alias — avoid in new code. `[A: 2026 API consolidation]`
- `core.available_devices` -> list[str]
- `core.get_property(dev, name)` / `core.set_property(dict)` — string property
  names accepted: `"FULL_DEVICE_NAME"`, `"OPTIMIZATION_CAPABILITIES"`,
  `"SUPPORTED_PROPERTIES"`, `"CACHE_DIR"`, `"PERFORMANCE_HINT"`,
  `"LOADED_FROM_CACHE"`, `"DEVICE_ARCHITECTURE"`. `[V mix]`
- `core.read_model(path)` -> Model; `model.reshape({name|idx: PartialShape})`
- `core.compile_model(model, device, config_dict)` -> CompiledModel
- `compiled.create_infer_request()` -> InferRequest; `.infer(inputs)` ;
  `.get_output_tensor(i).data` (numpy)
- `ov.AsyncInferQueue(compiled, jobs)`; `.set_callback(fn)`;
  `.start_async(inputs, userdata, share_inputs=False)`; `.wait_all()`

## Sources
- NPU Device (2026): https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.html
- NPU Device RST (master): https://raw.githubusercontent.com/openvinotoolkit/openvino/master/docs/articles_en/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.rst
- AUTO device selection (2026 / master RST): https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes/auto-device-selection.html
- Model Caching Overview: https://docs.openvino.ai/2024/openvino-workflow/running-inference/optimize-inference/optimizing-latency/model-caching-overview.html
- High-level Performance Hints: https://docs.openvino.ai/2025/openvino-workflow/running-inference/optimize-inference/high-level-performance-hints.html
- AsyncInferQueue Python API: https://docs.openvino.ai/2024/api/ie_python_api/_autosummary/openvino.runtime.AsyncInferQueue.html
- GenAI on NPU / VLMPipeline (2026): https://docs.openvino.ai/2026/openvino-workflow-generative/inference-with-genai/inference-with-genai-on-npu.html
- openvino 2026.2.0 PyPI metadata (numpy pin): https://pypi.org/pypi/openvino/2026.2.0/json
- linux-npu-driver: https://github.com/intel/linux-npu-driver

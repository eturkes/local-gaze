from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openvino import CompiledModel, Core

_log = logging.getLogger(__name__)


def make_core(cache_dir: str) -> Core:
    """Construct an OpenVINO ``Core`` with blob caching set (lazy import)."""
    import openvino as ov

    core = ov.Core()
    if cache_dir:
        # Set before any compile so first compile writes / later compiles import.
        core.set_property({"CACHE_DIR": cache_dir})
    return core


def compile_with_fallback(
    core: Core,
    model_path: str,
    static_shapes: dict[str, Any],
    device_order: list[str],
    hint: str = "LATENCY",
) -> tuple[CompiledModel, str]:
    """Read ``model_path``, reshape inputs to ``static_shapes``, compile on the first
    usable device in ``device_order`` with ``PERFORMANCE_HINT``. Returns
    ``(compiled, device_used)``; raises if every device fails.

    ``static_shapes`` maps input name (``any_name``) -> shape (list/tuple of ints).
    NPU mandates fully-static, batch=1 shapes, so we reshape every named input.
    """
    import openvino as ov

    model = core.read_model(model_path)
    if static_shapes:
        reshape_map = {name: ov.PartialShape(shape) for name, shape in static_shapes.items()}
        model.reshape(reshape_map)

    avail = set(core.available_devices)
    cfg = {"PERFORMANCE_HINT": hint}
    last_err: Exception | None = None
    for dev in device_order:
        if dev not in avail:
            _log.debug("device %s not available; skipping", dev)
            continue
        try:
            compiled = core.compile_model(model, dev, cfg)
            _log.info("compiled %s on %s (hint=%s)", model_path, dev, hint)
            return compiled, dev
        except Exception as exc:  # noqa: BLE001 - fail-soft per device, log + try next
            last_err = exc
            _log.warning("compile of %s on %s failed: %s", model_path, dev, exc)
    raise RuntimeError(
        f"no usable OpenVINO device for {model_path} (tried {device_order}): {last_err}"
    )


def npu_probe(core: Core) -> tuple[bool, str]:
    """Prove NPU usability by compiling+inferring a tiny static model on ``"NPU"``.

    Mirrors the verified host smoke (parameter [1,8] -> matmul identity -> relu).
    Listing ``"NPU"`` in ``available_devices`` is not sufficient — an op can still be
    rejected at compile; only a real compile+infer is honest. Returns
    ``(ok, FULL_DEVICE_NAME or error repr)``.
    """
    import numpy as np
    import openvino as ov

    if "NPU" not in core.available_devices:
        return False, "NPU not listed in available_devices"
    try:
        param = ov.opset13.parameter([1, 8], ov.Type.f32, name="input")
        eye = ov.opset13.constant(np.eye(8, dtype=np.float32))
        matmul = ov.opset13.matmul(param, eye, transpose_a=False, transpose_b=False)
        relu = ov.opset13.relu(matmul)
        model = ov.Model([relu], [param], "npu_probe")
        compiled = core.compile_model(model, "NPU")
        req = compiled.create_infer_request()
        req.infer({0: np.zeros((1, 8), np.float32)})
        return True, str(core.get_property("NPU", "FULL_DEVICE_NAME"))
    except Exception as exc:  # noqa: BLE001 - probe reports failure as detail, never raises
        return False, repr(exc)

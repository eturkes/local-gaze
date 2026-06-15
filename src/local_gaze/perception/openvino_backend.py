from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..types import GazePoint, HandSample, PerceptionResult

if TYPE_CHECKING:
    import numpy as np

    from ..config import Config

_log = logging.getLogger(__name__)

# Static input shapes (build-spec §6). NCHW for the OMZ gaze chain, NHWC for the
# converted MediaPipe hand models.
_FACE_SHAPE = {"data": [1, 3, 300, 300]}
_HEADPOSE_SHAPE = {"data": [1, 3, 60, 60]}
_LANDMARK_SHAPE = {"data": [1, 3, 60, 60]}
_GAZE_SHAPES = {
    "left_eye_image": [1, 3, 60, 60],
    "right_eye_image": [1, 3, 60, 60],
    "head_pose_angles": [1, 3],
}
_PALM_SHAPE = {"input": [1, 192, 192, 3]}
_HAND_LM_SHAPE = {"input": [1, 224, 224, 3]}

# Post-process tuning constants (host-tunable). Concrete reasonable defaults.
_FACE_CONF = 0.5
_PALM_CONF = 0.5
_NMS_IOU = 0.3
# Eye crop half-size as a fraction of the face box width (gives a ~square eye ROI).
_EYE_HALF_FRAC = 0.18
# Raw-gaze screen projection gain: maps the gaze vector's horizontal/vertical
# components (roughly [-1,1]) onto normalized screen offset from center. The
# calibration affine corrects per-user scale/offset, so this only needs to be a
# stable, monotonic default (host-tuned via calibration).
_GAZE_GAIN_X = 1.6
_GAZE_GAIN_Y = 1.6


class OpenVinoBackend:
    """Real CV backend: OMZ gaze chain + MediaPipe hand, NPU-first. Lazy openvino+cv2.

    Structurally satisfies ``perception.base.PerceptionBackend``.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._np: Any = None
        self._cv2: Any = None
        self._core: Any = None
        self._cam: Any = None
        self._frame_id = 0
        # compiled models
        self._face: Any = None
        self._headpose: Any = None
        self._landmark: Any = None
        self._gaze: Any = None
        self._palm: Any = None
        self._hand_lm: Any = None
        # chosen device per model (for info/status)
        self._devices: dict[str, str] = {}
        # palm anchor grid (decoded lazily at start)
        self._anchors: Any = None

    # ---- lifecycle -----------------------------------------------------------

    def _models_dir(self) -> Path:
        d = self._cfg.openvino.models_dir
        if d:
            return Path(d)
        repo_models = Path(__file__).resolve().parents[3] / "models"
        return repo_models

    def _cache_dir(self) -> str:
        if self._cfg.openvino.cache_dir:
            return self._cfg.openvino.cache_dir
        from .. import paths

        return str(paths.model_cache_dir())

    def start(self) -> None:
        import numpy as np

        from . import models as ovmodels
        from .camera import Camera

        self._np = np
        import cv2

        self._cv2 = cv2

        core = ovmodels.make_core(self._cache_dir())
        self._core = core

        mdir = self._models_dir()
        order = list(self._cfg.openvino.device_order)
        hint = self._cfg.openvino.performance_hint

        def compile_one(rel: str, shapes: dict[str, Any], key: str) -> Any:
            path = str(mdir / rel)
            compiled, dev = ovmodels.compile_with_fallback(core, path, shapes, order, hint)
            self._devices[key] = dev
            return compiled

        # Gaze chain (face -> head-pose + landmarks -> gaze).
        self._face = compile_one(
            "gaze/face-detection-retail-0004.xml", _FACE_SHAPE, "face"
        )
        self._headpose = compile_one(
            "gaze/head-pose-estimation-adas-0001.xml", _HEADPOSE_SHAPE, "headpose"
        )
        self._landmark = compile_one(
            "gaze/facial-landmarks-35-adas-0002.xml", _LANDMARK_SHAPE, "landmark"
        )
        self._gaze = compile_one(
            "gaze/gaze-estimation-adas-0002.xml", _GAZE_SHAPES, "gaze"
        )
        # Hand (palm detect + hand landmark).
        self._palm = compile_one("hand/palm.xml", _PALM_SHAPE, "palm")
        self._hand_lm = compile_one("hand/landmark.xml", _HAND_LM_SHAPE, "hand_landmark")

        self._anchors = _ssd_anchors(np)

        cam = Camera(self._cfg.camera.device, self._cfg.camera.width, self._cfg.camera.height)
        cam.open()
        self._cam = cam
        _log.info("openvino backend started; devices=%s", self._devices)

    def stop(self) -> None:
        if self._cam is not None:
            self._cam.close()
            self._cam = None
        # Drop compiled handles; OpenVINO frees on GC.
        self._face = self._headpose = self._landmark = self._gaze = None
        self._palm = self._hand_lm = None
        self._core = None

    # ---- per-frame -----------------------------------------------------------

    def read(self) -> PerceptionResult:
        ts = time.monotonic()
        self._frame_id += 1
        frame = self._cam.read()  # BGR HxWx3
        gaze = self._infer_gaze(frame)
        hand = self._infer_hand(frame)
        return PerceptionResult(ts=ts, gaze=gaze, hand=hand, frame_id=self._frame_id)

    # ---- gaze chain ----------------------------------------------------------

    def _infer_gaze(self, frame: np.ndarray) -> GazePoint | None:
        np = self._np

        face = self._detect_face(frame)
        if face is None:
            return None
        x0, y0, x1, y1, conf = face
        fw = x1 - x0
        fh = y1 - y0
        if fw < 8 or fh < 8:
            return None
        face_crop = frame[y0:y1, x0:x1]

        yaw, pitch, roll = self._head_pose(face_crop)
        landmarks = self._landmarks(face_crop)  # (35,2) in face-crop normalized coords

        left_eye = self._eye_crop(frame, x0, y0, fw, fh, landmarks, (0, 1))
        right_eye = self._eye_crop(frame, x0, y0, fw, fh, landmarks, (2, 3))
        if left_eye is None or right_eye is None:
            return None

        gaze_vec = self._gaze_vector(left_eye, right_eye, (yaw, pitch, roll))
        nx, ny = _gaze_to_screen(np, gaze_vec, roll)
        return GazePoint(nx=nx, ny=ny, confidence=float(conf), yaw=yaw, pitch=pitch)

    def _detect_face(self, frame: np.ndarray) -> tuple[int, int, int, int, float] | None:
        np = self._np
        h, w = frame.shape[:2]
        blob = _to_nchw(np, self._cv2, frame, 300, 300)
        out = self._infer(self._face, blob)  # [1,1,200,7]
        dets = np.asarray(out).reshape(-1, 7)
        best: tuple[int, int, int, int, float] | None = None
        best_conf = _FACE_CONF
        for det in dets:
            conf = float(det[2])
            if conf < best_conf:
                continue
            x0 = int(round(float(det[3]) * w))
            y0 = int(round(float(det[4]) * h))
            x1 = int(round(float(det[5]) * w))
            y1 = int(round(float(det[6]) * h))
            x0 = max(0, min(x0, w - 1))
            y0 = max(0, min(y0, h - 1))
            x1 = max(0, min(x1, w))
            y1 = max(0, min(y1, h))
            if x1 <= x0 or y1 <= y0:
                continue
            best_conf = conf
            best = (x0, y0, x1, y1, conf)
        return best

    def _head_pose(self, face_crop: np.ndarray) -> tuple[float, float, float]:
        np = self._np
        blob = _to_nchw(np, self._cv2, face_crop, 60, 60)
        req = self._headpose.create_infer_request()
        req.infer({0: blob})
        # Named outputs: angle_y_fc (yaw), angle_p_fc (pitch), angle_r_fc (roll).
        vals: dict[str, float] = {}
        for out in self._headpose.outputs:
            name = out.any_name
            vals[name] = float(np.asarray(req.get_tensor(out).data).ravel()[0])
        yaw = vals.get("angle_y_fc", 0.0)
        pitch = vals.get("angle_p_fc", 0.0)
        roll = vals.get("angle_r_fc", 0.0)
        return yaw, pitch, roll

    def _landmarks(self, face_crop: np.ndarray) -> np.ndarray:
        np = self._np
        blob = _to_nchw(np, self._cv2, face_crop, 60, 60)
        out = self._infer(self._landmark, blob)  # [1,70]
        flat = np.asarray(out).ravel()
        return flat.reshape(-1, 2)  # (35,2) normalized to the face crop

    def _eye_crop(
        self,
        frame: np.ndarray,
        fx: int,
        fy: int,
        fw: int,
        fh: int,
        landmarks: np.ndarray,
        corner_idx: tuple[int, int],
    ) -> np.ndarray | None:
        np = self._np
        h, w = frame.shape[:2]
        i0, i1 = corner_idx
        # Eye center = midpoint of the two eye-corner landmarks, mapped to full frame.
        cx_n = (float(landmarks[i0, 0]) + float(landmarks[i1, 0])) * 0.5
        cy_n = (float(landmarks[i0, 1]) + float(landmarks[i1, 1])) * 0.5
        cx = fx + cx_n * fw
        cy = fy + cy_n * fh
        half = _EYE_HALF_FRAC * fw
        ex0 = int(round(cx - half))
        ey0 = int(round(cy - half))
        ex1 = int(round(cx + half))
        ey1 = int(round(cy + half))
        ex0 = max(0, ex0)
        ey0 = max(0, ey0)
        ex1 = min(w, ex1)
        ey1 = min(h, ey1)
        if ex1 - ex0 < 4 or ey1 - ey0 < 4:
            return None
        return _to_nchw(np, self._cv2, frame[ey0:ey1, ex0:ex1], 60, 60)

    def _gaze_vector(
        self, left_eye: np.ndarray, right_eye: np.ndarray, hpa: tuple[float, float, float]
    ) -> np.ndarray:
        np = self._np
        angles = np.array([[hpa[0], hpa[1], hpa[2]]], dtype=np.float32)
        req = self._gaze.create_infer_request()
        req.infer(
            {
                "left_eye_image": left_eye,
                "right_eye_image": right_eye,
                "head_pose_angles": angles,
            }
        )
        return np.asarray(req.get_output_tensor(0).data).ravel()[:3]

    # ---- hand chain ----------------------------------------------------------

    def _infer_hand(self, frame: np.ndarray) -> HandSample:
        det = self._detect_palm(frame)
        if det is None:
            return HandSample(present=False)
        px0, py0, px1, py1, score = det
        h, w = frame.shape[:2]
        hand_crop = self._crop(frame, px0, py0, px1, py1)
        if hand_crop is None:
            cx = ((px0 + px1) * 0.5) / w
            cy = ((py0 + py1) * 0.5) / h
            return HandSample(present=True, cx=_clip01(cx), cy=_clip01(cy), confidence=score)

        lm = self._hand_landmarks(hand_crop)  # (21,3) normalized to crop
        # Hand center = mean(wrist=0, middle-MCP=9), mapped crop -> full frame -> normalized.
        cw = px1 - px0
        ch = py1 - py0
        mx_n = (float(lm[0, 0]) + float(lm[9, 0])) * 0.5
        my_n = (float(lm[0, 1]) + float(lm[9, 1])) * 0.5
        cx = (px0 + mx_n * cw) / w
        cy = (py0 + my_n * ch) / h
        return HandSample(present=True, cx=_clip01(cx), cy=_clip01(cy), confidence=score)

    def _detect_palm(self, frame: np.ndarray) -> tuple[int, int, int, int, float] | None:
        np = self._np
        h, w = frame.shape[:2]
        blob = _to_nhwc_rgb(np, self._cv2, frame, 192, 192, scale=1.0 / 127.5, mean=127.5)
        req = self._palm.create_infer_request()
        req.infer({0: blob})
        scores = None
        boxes = None
        for out in self._palm.outputs:
            arr = np.asarray(req.get_tensor(out).data)
            if arr.shape[-1] == 1:
                scores = arr.reshape(-1)
            else:
                boxes = arr.reshape(arr.shape[1], arr.shape[2])
        if scores is None or boxes is None:
            return None
        # Sigmoid on raw classifier logits -> confidence per anchor.
        probs = 1.0 / (1.0 + np.exp(-np.clip(scores, -50.0, 50.0)))
        keep = probs >= _PALM_CONF
        if not bool(keep.any()):
            return None
        anchors = self._anchors
        # MediaPipe palm box decode: box center/size are anchor-relative in 192px units.
        idx = np.nonzero(keep)[0]
        cand_boxes = []
        cand_scores = []
        for i in idx:
            ax, ay = float(anchors[i, 0]), float(anchors[i, 1])
            dx = float(boxes[i, 0]) / 192.0 + ax
            dy = float(boxes[i, 1]) / 192.0 + ay
            bw = float(boxes[i, 2]) / 192.0
            bh = float(boxes[i, 3]) / 192.0
            x0 = (dx - bw * 0.5) * w
            y0 = (dy - bh * 0.5) * h
            x1 = (dx + bw * 0.5) * w
            y1 = (dy + bh * 0.5) * h
            cand_boxes.append((x0, y0, x1, y1))
            cand_scores.append(float(probs[i]))
        chosen = _nms(np, cand_boxes, cand_scores, _NMS_IOU)
        if chosen is None:
            return None
        x0, y0, x1, y1, sc = chosen
        ix0 = max(0, int(round(x0)))
        iy0 = max(0, int(round(y0)))
        ix1 = min(w, int(round(x1)))
        iy1 = min(h, int(round(y1)))
        if ix1 - ix0 < 8 or iy1 - iy0 < 8:
            return None
        return ix0, iy0, ix1, iy1, sc

    def _hand_landmarks(self, hand_crop: np.ndarray) -> np.ndarray:
        np = self._np
        blob = _to_nhwc_rgb(np, self._cv2, hand_crop, 224, 224, scale=1.0 / 255.0, mean=0.0)
        req = self._hand_lm.create_infer_request()
        req.infer({0: blob})
        coords = None
        for out in self._hand_lm.outputs:
            arr = np.asarray(req.get_tensor(out).data).ravel()
            if arr.size >= 63:
                coords = arr[:63].reshape(21, 3)
                break
        if coords is None:
            return np.full((21, 3), 0.5, dtype=np.float32)
        # Landmarks come out in 224px units; normalize to crop [0,1].
        out = coords.copy().astype(np.float32)
        out[:, 0] /= 224.0
        out[:, 1] /= 224.0
        return out

    def _crop(
        self, frame: np.ndarray, x0: int, y0: int, x1: int, y1: int
    ) -> np.ndarray | None:
        h, w = frame.shape[:2]
        x0 = max(0, min(x0, w - 1))
        y0 = max(0, min(y0, h - 1))
        x1 = max(0, min(x1, w))
        y1 = max(0, min(y1, h))
        if x1 - x0 < 4 or y1 - y0 < 4:
            return None
        return frame[y0:y1, x0:x1]

    # ---- helpers -------------------------------------------------------------

    def _infer(self, compiled: Any, blob: np.ndarray) -> Any:
        req = compiled.create_infer_request()
        req.infer({0: blob})
        return req.get_output_tensor(0).data

    @property
    def info(self) -> dict:
        full_names: dict[str, str] = {}
        if self._core is not None:
            for dev in set(self._devices.values()):
                try:
                    full_names[dev] = str(self._core.get_property(dev, "FULL_DEVICE_NAME"))
                except Exception:  # noqa: BLE001 - info is best-effort
                    full_names[dev] = dev
        return {
            "backend": "openvino",
            "device": self._devices,
            "device_names": full_names,
            "models": list(self._devices.keys()),
            "camera": self._cfg.camera.device,
        }


# ---- module-level numpy post-process (pure, host-tunable) --------------------


def _clip01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def _to_nchw(np: Any, cv2: Any, img: np.ndarray, w: int, h: int) -> np.ndarray:
    """Resize a BGR crop to (h,w) and pack to NCHW float32 (model-native BGR)."""
    resized = cv2.resize(img, (w, h))
    arr = resized.astype(np.float32).transpose(2, 0, 1)
    return arr[np.newaxis, ...]


def _to_nhwc_rgb(
    np: Any, cv2: Any, img: np.ndarray, w: int, h: int, *, scale: float, mean: float
) -> np.ndarray:
    """Resize BGR->RGB, normalize ``(x-mean)*scale``, pack NHWC float32 (MediaPipe)."""
    resized = cv2.resize(img, (w, h))
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32)
    rgb = (rgb - mean) * scale
    return rgb[np.newaxis, ...]


def _gaze_to_screen(np: Any, gaze_vec: np.ndarray, roll_deg: float) -> tuple[float, float]:
    """Project a (non-unit) gaze vector to a normalized screen point in [0,1]^2.

    OMZ gaze output is camera-frame Cartesian; the demo roll-compensates it. We
    rotate (gx,gy) by -roll, then map horizontal/vertical components (camera +x is
    the user's left, screen +x is right, so flip x) onto a center-relative screen
    offset. Raw/uncalibrated: the calibration affine fixes per-user scale+offset.
    """
    gx, gy, gz = (float(gaze_vec[0]), float(gaze_vec[1]), float(gaze_vec[2]))
    norm = math.sqrt(gx * gx + gy * gy + gz * gz) or 1.0
    gx, gy = gx / norm, gy / norm
    r = math.radians(roll_deg)
    cos_r, sin_r = math.cos(r), math.sin(r)
    rx = gx * cos_r + gy * sin_r
    ry = -gx * sin_r + gy * cos_r
    nx = 0.5 - rx * _GAZE_GAIN_X * 0.5  # camera-left -> screen-right flip
    ny = 0.5 - ry * _GAZE_GAIN_Y * 0.5  # up gaze -> top of screen
    return _clip01(nx), _clip01(ny)


def _ssd_anchors(np: Any) -> np.ndarray:
    """MediaPipe palm-detection-full SSD anchor centers (192x192, 2016 anchors).

    Two feature-map strides (24x24 with 2 anchors, 12x12 with 6 anchors) give
    24*24*2 + 12*12*6 = 1152 + 864 = 2016 anchors. Only the (x,y) center is needed
    for box decode (anchor w/h are unit-normalized for the full model).
    """
    anchors: list[tuple[float, float]] = []
    for grid, n_per in ((24, 2), (12, 6)):
        for gy in range(grid):
            cy = (gy + 0.5) / grid
            for gx in range(grid):
                cx = (gx + 0.5) / grid
                for _ in range(n_per):
                    anchors.append((cx, cy))
    return np.asarray(anchors, dtype=np.float32)


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    iw = max(0.0, ix1 - ix0)
    ih = max(0.0, iy1 - iy0)
    inter = iw * ih
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


def _nms(
    np: Any,
    boxes: list[tuple[float, float, float, float]],
    scores: list[float],
    iou_thresh: float,
) -> tuple[float, float, float, float, float] | None:
    """Greedy IoU NMS; returns the highest-scoring survivor (one hand for flick)."""
    if not boxes:
        return None
    order = sorted(range(len(boxes)), key=lambda i: scores[i], reverse=True)
    suppressed = [False] * len(boxes)
    survivors: list[int] = []
    for i in order:
        if suppressed[i]:
            continue
        survivors.append(i)
        for j in order:
            if j == i or suppressed[j]:
                continue
            if _iou(boxes[i], boxes[j]) > iou_thresh:
                suppressed[j] = True
    best = survivors[0]
    bx0, by0, bx1, by1 = boxes[best]
    return (bx0, by0, bx1, by1, scores[best])

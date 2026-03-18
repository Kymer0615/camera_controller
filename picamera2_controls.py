from __future__ import annotations

from dataclasses import dataclass

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None

try:
    from .v4l2 import ControlInfo
except ImportError:
    from v4l2 import ControlInfo


@dataclass(slots=True)
class Picamera2ControlSet:
    controls: dict[str, ControlInfo]


def picamera2_available() -> bool:
    return Picamera2 is not None


def list_picamera2_controls(camera_index: int = 0) -> dict[str, ControlInfo]:
    if Picamera2 is None:
        return {}
    camera = Picamera2(camera_index)
    try:
        return controls_from_camera_controls(camera.camera_controls)
    finally:
        camera.close()


def controls_from_camera_controls(camera_controls: dict[str, tuple[object, object, object]]) -> dict[str, ControlInfo]:
    controls: dict[str, ControlInfo] = {}
    for name, descriptor in sorted(camera_controls.items()):
        if not isinstance(descriptor, tuple) or len(descriptor) != 3:
            continue
        min_value, max_value, default = descriptor
        kind = _control_kind(default, min_value, max_value)
        controls[name] = ControlInfo(
            name=name,
            kind=kind,
            min_value=min_value,
            max_value=max_value,
            step=0.1 if kind == "float" else None,
            default=default,
            value=default,
        )
    return controls


def _control_kind(default: object, min_value: object, max_value: object) -> str:
    values = (default, min_value, max_value)
    if any(isinstance(value, bool) for value in values):
        return "bool"
    if any(isinstance(value, float) for value in values):
        return "float"
    if any(isinstance(value, int) for value in values):
        return "int"
    if isinstance(default, (tuple, list)):
        return "tuple"
    return "text"

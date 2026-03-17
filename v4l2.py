from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class CameraDevice:
    index: int
    name: str
    path: str


@dataclass(slots=True)
class ControlInfo:
    name: str
    kind: str
    min_value: int | None = None
    max_value: int | None = None
    step: int | None = None
    default: int | None = None
    value: int | None = None
    flags: list[str] = field(default_factory=list)
    menu_items: dict[int, str] = field(default_factory=dict)


@dataclass(slots=True)
class CameraCapabilities:
    formats: dict[str, list[tuple[int, int]]]
    controls: dict[str, ControlInfo]


class V4L2Error(RuntimeError):
    pass


def _run_v4l2(*args: str) -> str:
    try:
        result = subprocess.run(
            ["v4l2-ctl", *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise V4L2Error("`v4l2-ctl` was not found. Install `v4l-utils` first.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() or exc.stdout.strip()
        raise V4L2Error(stderr or f"`v4l2-ctl {' '.join(args)}` failed.") from exc
    return result.stdout


def list_devices() -> list[CameraDevice]:
    output = _run_v4l2("--list-devices")
    devices: list[CameraDevice] = []
    blocks = [block.strip() for block in output.split("\n\n") if block.strip()]
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        name = lines[0].rstrip(":")
        for line in lines[1:]:
            path = line.strip()
            if not path.startswith("/dev/video"):
                continue
            try:
                index = int(Path(path).name.replace("video", ""))
            except ValueError:
                continue
            devices.append(CameraDevice(index=index, name=name, path=path))
    return sorted(devices, key=lambda item: item.index)


def list_formats(device_path: str) -> dict[str, list[tuple[int, int]]]:
    output = _run_v4l2("--device", device_path, "--list-formats-ext")
    formats: dict[str, list[tuple[int, int]]] = {}
    current_format: str | None = None
    format_pattern = re.compile(r"\[\d+\]: '([^']+)'")
    size_pattern = re.compile(r"Size:\s+Discrete\s+(\d+)x(\d+)")
    stepwise_pattern = re.compile(
        r"Size:\s+Stepwise\s+(\d+)x(\d+)\s*-\s*(\d+)x(\d+)\s+with step\s+(\d+)/(\d+)"
    )
    for raw_line in output.splitlines():
        line = raw_line.strip()
        format_match = format_pattern.search(line)
        if format_match:
            current_format = format_match.group(1)
            formats.setdefault(current_format, [])
            continue
        size_match = size_pattern.search(line)
        if size_match and current_format:
            size = (int(size_match.group(1)), int(size_match.group(2)))
            if size not in formats[current_format]:
                formats[current_format].append(size)
            continue
        stepwise_match = stepwise_pattern.search(line)
        if stepwise_match and current_format:
            min_width = int(stepwise_match.group(1))
            min_height = int(stepwise_match.group(2))
            max_width = int(stepwise_match.group(3))
            max_height = int(stepwise_match.group(4))
            if _looks_like_unconfigured_stepwise_range(min_width, min_height, max_width, max_height):
                continue
            for size in ((max_width, max_height), (min_width, min_height)):
                if size not in formats[current_format]:
                    formats[current_format].append(size)
    return formats


def _looks_like_unconfigured_stepwise_range(
    min_width: int,
    min_height: int,
    max_width: int,
    max_height: int,
) -> bool:
    if max_width >= 8192 or max_height >= 8192:
        return True
    if max_width * max_height >= 64_000_000:
        return True
    if min_width == 16 and min_height == 16 and max_width == max_height:
        return True
    return False


def list_controls(device_path: str) -> dict[str, ControlInfo]:
    output = _run_v4l2("--device", device_path, "--list-ctrls-menus")
    controls: dict[str, ControlInfo] = {}
    current: ControlInfo | None = None
    control_pattern = re.compile(
        r"^\s*([a-zA-Z0-9_]+)\s+0x[0-9a-f]+\s+\(([^)]+)\)\s*:\s*(.*)$"
    )
    menu_pattern = re.compile(r"^\s+(\d+):\s+(.*)$")
    kv_pattern = re.compile(r"(\w+)=(-?\d+)")
    for line in output.splitlines():
        control_match = control_pattern.match(line)
        if control_match:
            name, kind, remainder = control_match.groups()
            values = {key: int(value) for key, value in kv_pattern.findall(remainder)}
            flags: list[str] = []
            if "flags=" in remainder:
                flags = [flag.strip() for flag in remainder.split("flags=", 1)[1].split(",")]
            current = ControlInfo(
                name=name,
                kind=kind,
                min_value=values.get("min"),
                max_value=values.get("max"),
                step=values.get("step"),
                default=values.get("default"),
                value=values.get("value"),
                flags=flags,
            )
            controls[name] = current
            continue
        menu_match = menu_pattern.match(line)
        if menu_match and current and current.kind == "menu":
            current.menu_items[int(menu_match.group(1))] = menu_match.group(2).strip()
    return controls


def get_capabilities(device_path: str) -> CameraCapabilities:
    return CameraCapabilities(
        formats=list_formats(device_path),
        controls=list_controls(device_path),
    )


def set_format(device_path: str, pixel_format: str, resolution: tuple[int, int]) -> None:
    width, height = resolution
    _run_v4l2(
        "--device",
        device_path,
        f"--set-fmt-video=width={width},height={height},pixelformat={pixel_format}",
    )


def apply_controls(device_path: str, values: dict[str, int]) -> None:
    if not values:
        return
    assignments = ",".join(f"{name}={value}" for name, value in values.items())
    _run_v4l2("--device", device_path, "--set-ctrl", assignments)

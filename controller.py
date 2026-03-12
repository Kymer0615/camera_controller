from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path

import cv2

try:
    from .config_ui import ConfigWindow, SessionConfig
    from .v4l2 import V4L2Error, apply_controls
except ImportError:
    from config_ui import ConfigWindow, SessionConfig
    from v4l2 import V4L2Error, apply_controls


WINDOW_NAME = "Camera Preview"


def _fourcc(codec: str) -> int:
    return cv2.VideoWriter_fourcc(*codec)


def _open_capture(config: SessionConfig) -> cv2.VideoCapture:
    capture = cv2.VideoCapture(config.device_index, cv2.CAP_V4L2)
    capture.set(cv2.CAP_PROP_FOURCC, _fourcc(config.pixel_format))
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.resolution[0])
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.resolution[1])
    if not capture.isOpened():
        raise RuntimeError(f"Could not open camera {config.device_path}.")
    return capture


def _save_session_metadata(config: SessionConfig, session_dir: Path) -> None:
    metadata_path = session_dir / "session_config.json"
    metadata_path.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")


def _capture_path(config: SessionConfig, session_dir: Path, counter: int) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return session_dir / f"{config.file_prefix}_{counter:04d}_{timestamp}.{config.image_extension}"


def run_preview(config: SessionConfig) -> None:
    session_dir = Path(config.save_directory).expanduser().resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    _save_session_metadata(config, session_dir)

    if config.controls:
        apply_controls(config.device_path, config.controls)

    capture = _open_capture(config)
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, config.preview_width, config.preview_height)

    zoom = max(config.initial_zoom, 0.1)
    fullscreen = False
    frame_counter = 0
    saved_images: list[Path] = []

    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError("Failed to read a frame from the camera.")

            display = _scale_frame(frame, zoom)
            overlay = _add_overlay(display, zoom, frame.shape[1], frame.shape[0], session_dir, len(saved_images))
            cv2.imshow(WINDOW_NAME, overlay)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key in (ord("+"), ord("=")):
                zoom = min(zoom * 1.25, 16.0)
            elif key in (ord("-"), ord("_")):
                zoom = max(zoom / 1.25, 0.1)
            elif key == ord("0"):
                zoom = 1.0
            elif key == ord("c"):
                path = _capture_path(config, session_dir, frame_counter)
                if cv2.imwrite(str(path), frame):
                    saved_images.append(path)
                    frame_counter += 1
                    print(f"Saved image: {path}")
            elif key in (ord("d"), 8, 127):
                if saved_images:
                    last = saved_images.pop()
                    if last.exists():
                        last.unlink()
                        print(f"Deleted image: {last}")
            elif key == ord("f"):
                fullscreen = not fullscreen
                mode = cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL
                cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, mode)
            elif key == ord("h"):
                _print_shortcuts()
    finally:
        capture.release()
        cv2.destroyAllWindows()


def _scale_frame(frame, zoom: float):
    if abs(zoom - 1.0) < 1e-6:
        return frame
    height, width = frame.shape[:2]
    new_width = max(int(width * zoom), 1)
    new_height = max(int(height * zoom), 1)
    return cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_LINEAR)


def _add_overlay(frame, zoom: float, width: int, height: int, output_dir: Path, save_count: int):
    overlay = frame.copy()
    lines = [
        f"Resolution: {width}x{height}",
        f"Zoom: {zoom:.2f}x",
        f"Saved: {save_count}",
        f"Folder: {output_dir}",
        "Keys: c capture | d delete last | +/- zoom | 0 reset | f fullscreen | h help | q quit",
    ]
    y = 28
    for line in lines:
        cv2.putText(
            overlay,
            line,
            (12, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            overlay,
            line,
            (12, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 0, 0),
            1,
            cv2.LINE_AA,
        )
        y += 28
    return overlay


def _print_shortcuts() -> None:
    print("Keyboard shortcuts:")
    print("  c : capture current frame")
    print("  d : delete previously captured image")
    print("  + : zoom in")
    print("  - : zoom out")
    print("  0 : reset zoom to 1.0x")
    print("  f : toggle fullscreen")
    print("  h : print shortcuts")
    print("  q / Esc : quit preview")


def _load_cli_config(path: str) -> SessionConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return SessionConfig(
        device_index=int(data["device_index"]),
        device_path=data["device_path"],
        pixel_format=data["pixel_format"],
        resolution=tuple(data["resolution"]),
        save_directory=data["save_directory"],
        file_prefix=data.get("file_prefix", "capture"),
        image_extension=data.get("image_extension", "png"),
        initial_zoom=float(data.get("initial_zoom", 1.0)),
        preview_width=int(data.get("preview_width", 1280)),
        preview_height=int(data.get("preview_height", 720)),
        controls={key: int(value) for key, value in data.get("controls", {}).items()},
    )


def launch(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Interactive V4L2 camera controller.")
    parser.add_argument(
        "--config",
        help="Optional JSON session config to prefill or run directly.",
    )
    parser.add_argument(
        "--run-config-direct",
        action="store_true",
        help="Skip the setup window and run the JSON config directly.",
    )
    args = parser.parse_args(argv)

    try:
        if args.config and args.run_config_direct:
            config = _load_cli_config(args.config)
        else:
            window = ConfigWindow(config_path=args.config)
            config = window.run()
        _print_shortcuts()
        run_preview(config)
        return 0
    except (V4L2Error, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1

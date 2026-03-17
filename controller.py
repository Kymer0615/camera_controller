from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2

try:
    from .config_ui import ConfigWindow, SessionConfig
    from .v4l2 import ControlInfo, V4L2Error, apply_controls, get_capabilities
except ImportError:
    from config_ui import ConfigWindow, SessionConfig
    from v4l2 import ControlInfo, V4L2Error, apply_controls, get_capabilities


WINDOW_NAME = "Camera Preview"
RUNTIME_WINDOW_NAME = "Runtime Controls"
BAYER_CONVERSIONS = {
    "BA81": cv2.COLOR_BAYER_BG2BGR,
    "BGGR": cv2.COLOR_BAYER_BG2BGR,
    "GBRG": cv2.COLOR_BAYER_GB2BGR,
    "GRBG": cv2.COLOR_BAYER_GR2BGR,
    "RGGB": cv2.COLOR_BAYER_RG2BGR,
    "BG10": cv2.COLOR_BAYER_BG2BGR,
    "GB10": cv2.COLOR_BAYER_GB2BGR,
    "BA10": cv2.COLOR_BAYER_GR2BGR,
    "RG10": cv2.COLOR_BAYER_RG2BGR,
    "BG12": cv2.COLOR_BAYER_BG2BGR,
    "GB12": cv2.COLOR_BAYER_GB2BGR,
    "BA12": cv2.COLOR_BAYER_GR2BGR,
    "RG12": cv2.COLOR_BAYER_RG2BGR,
    "BG16": cv2.COLOR_BAYER_BG2BGR,
    "GB16": cv2.COLOR_BAYER_GB2BGR,
    "BA16": cv2.COLOR_BAYER_GR2BGR,
    "RG16": cv2.COLOR_BAYER_RG2BGR,
}


def _fourcc(codec: str) -> int:
    return cv2.VideoWriter_fourcc(*codec)


def _open_capture(config: SessionConfig) -> cv2.VideoCapture:
    _validate_resolution(config)
    capture = cv2.VideoCapture(config.device_index, cv2.CAP_V4L2)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    capture.set(cv2.CAP_PROP_FOURCC, _fourcc(config.pixel_format))
    if _is_raw_format(config.pixel_format):
        capture.set(cv2.CAP_PROP_CONVERT_RGB, 0)
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.resolution[0])
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.resolution[1])
    if not capture.isOpened():
        raise RuntimeError(f"Could not open camera {config.device_path}.")
    return capture


def _validate_resolution(config: SessionConfig) -> None:
    width, height = config.resolution
    if width >= 8192 or height >= 8192 or width * height >= 64_000_000:
        raise ValueError(
            f"Resolution {width}x{height} looks invalid for {config.device_path}. "
            "This Pi camera node is likely exposing an unconfigured stepwise range. "
            "Enter the real sensor mode manually, for example 1456x1088 for the global shutter camera."
        )


def _is_raw_format(pixel_format: str) -> bool:
    return pixel_format in BAYER_CONVERSIONS


def _normalize_raw_frame(frame):
    if getattr(frame.dtype, "kind", "") == "u" and getattr(frame.dtype, "itemsize", 0) == 1:
        return frame
    return cv2.normalize(frame, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)


def _prepare_frame(frame, pixel_format: str, raw_processing_enabled: bool):
    if frame is None:
        raise RuntimeError("Camera returned an empty frame.")

    if not _is_raw_format(pixel_format):
        return frame, frame

    if frame.ndim == 3 and frame.shape[2] == 1:
        frame = frame[:, :, 0]
    if frame.ndim != 2:
        raise RuntimeError(
            f"Unsupported raw frame shape {frame.shape!r} for pixel format {pixel_format}."
        )

    raw_frame = frame.copy()
    preview_source = _normalize_raw_frame(raw_frame)
    if raw_processing_enabled:
        preview_frame = cv2.cvtColor(preview_source, BAYER_CONVERSIONS[pixel_format])
    else:
        preview_frame = cv2.cvtColor(preview_source, cv2.COLOR_GRAY2BGR)
    return preview_frame, raw_frame


def _write_frame(path: Path, preview_frame, save_frame, pixel_format: str, raw_processing_enabled: bool) -> bool:
    extension = path.suffix.lower()
    frame_to_save = save_frame
    if _is_raw_format(pixel_format) and (raw_processing_enabled or extension in {".jpg", ".jpeg", ".bmp"}):
        frame_to_save = preview_frame
    return cv2.imwrite(str(path), frame_to_save)


def _processing_label(pixel_format: str, raw_processing_enabled: bool) -> str:
    if not _is_raw_format(pixel_format):
        return "standard"
    return "basic" if raw_processing_enabled else "raw"


def _save_session_metadata(config: SessionConfig, session_dir: Path) -> None:
    metadata_path = session_dir / "session_config.json"
    metadata_path.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")


def _capture_path(config: SessionConfig, session_dir: Path, counter: int) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return session_dir / f"{config.file_prefix}_{counter:04d}_{timestamp}.{config.image_extension}"


def _session_dir(config: SessionConfig) -> Path:
    session_dir = Path(config.save_directory).expanduser().resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


class RuntimeControlWindow:
    def __init__(self, config: SessionConfig, on_config_changed) -> None:
        self.config = config
        self.on_config_changed = on_config_changed
        self.capabilities = get_capabilities(config.device_path)
        self.root = tk.Tk()
        self.root.title(RUNTIME_WINDOW_NAME)
        self.root.geometry("720x760")
        self.root.minsize(620, 560)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.closed = False
        self.status_var = tk.StringVar(value="Ready")
        self.save_dir_var = tk.StringVar(value=config.save_directory)
        self.prefix_var = tk.StringVar(value=config.file_prefix)
        self.extension_var = tk.StringVar(value=config.image_extension)
        self.zoom_var = tk.StringVar(value=str(config.initial_zoom))
        self.raw_processing_var = tk.BooleanVar(value=config.raw_processing_enabled)
        self.control_vars: dict[str, tk.Variable] = {}
        self.control_widgets: list[tk.Widget] = []

        self._build_layout()
        self._populate_from_config(config)

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        top = ttk.Frame(self.root, padding=12)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)

        ttk.Label(top, text="Device").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Label(top, text=self.config.device_path).grid(row=0, column=1, columnspan=3, sticky="w", pady=6)

        ttk.Label(top, text="Format").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Label(top, text=self.config.pixel_format).grid(row=1, column=1, sticky="w", pady=6)
        ttk.Label(top, text="Resolution").grid(row=1, column=2, sticky="w", padx=(8, 8), pady=6)
        ttk.Label(top, text=f"{self.config.resolution[0]}x{self.config.resolution[1]}").grid(
            row=1,
            column=3,
            sticky="w",
            pady=6,
        )

        ttk.Label(top, text="Save Folder").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(top, textvariable=self.save_dir_var).grid(row=2, column=1, columnspan=2, sticky="ew", pady=6)
        ttk.Button(top, text="Browse", command=self._pick_directory).grid(row=2, column=3, sticky="ew", pady=6)

        ttk.Label(top, text="Prefix").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(top, textvariable=self.prefix_var).grid(row=3, column=1, sticky="ew", pady=6)

        ttk.Label(top, text="Extension").grid(row=3, column=2, sticky="w", padx=(8, 8), pady=6)
        ttk.Combobox(
            top,
            textvariable=self.extension_var,
            state="readonly",
            values=("png", "jpg", "bmp", "tiff"),
        ).grid(row=3, column=3, sticky="ew", pady=6)

        ttk.Label(top, text="Preview Zoom").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(top, textvariable=self.zoom_var).grid(row=4, column=1, sticky="ew", pady=6)

        self.raw_processing_check = ttk.Checkbutton(
            top,
            text="Basic raw processing (normalize + demosaic)",
            variable=self.raw_processing_var,
        )
        self.raw_processing_check.grid(row=4, column=2, columnspan=2, sticky="w", pady=6)
        if not _is_raw_format(self.config.pixel_format):
            self.raw_processing_check.configure(state="disabled")

        controls_frame = ttk.LabelFrame(self.root, text="Live Camera Controls", padding=8)
        controls_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        controls_frame.rowconfigure(0, weight=1)
        controls_frame.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(controls_frame, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(controls_frame, orient="vertical", command=self.canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.controls_container = ttk.Frame(self.canvas)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.controls_container, anchor="nw")
        self.controls_container.bind("<Configure>", self._on_controls_configure)
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        self._render_controls()

        bottom = ttk.Frame(self.root, padding=(12, 0, 12, 12))
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        actions = ttk.Frame(bottom)
        actions.grid(row=0, column=1, sticky="e")
        ttk.Button(actions, text="Load Config", command=self.load_config_dialog).grid(row=0, column=0)
        ttk.Button(actions, text="Save Config", command=self.save_config_dialog).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(actions, text="Apply", command=self.apply_current_settings).grid(row=0, column=2, padx=(8, 0))

    def _on_controls_configure(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_resize(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

    def _pick_directory(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.save_dir_var.get() or str(Path.cwd()))
        if selected:
            self.save_dir_var.set(selected)

    def _render_controls(self) -> None:
        for widget in self.control_widgets:
            widget.destroy()
        self.control_widgets.clear()
        self.control_vars.clear()

        row = 0
        for name, control in sorted(self.capabilities.controls.items()):
            if "inactive" in control.flags:
                continue
            label = ttk.Label(self.controls_container, text=self._format_control_label(control))
            label.grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            variable, widget = self._build_control_widget(control)
            widget.grid(row=row, column=1, sticky="ew", pady=4)
            self.controls_container.columnconfigure(1, weight=1)
            self.control_vars[name] = variable
            self.control_widgets.append(label)
            self.control_widgets.append(widget)
            row += 1

    def _build_control_widget(self, control: ControlInfo) -> tuple[tk.Variable, tk.Widget]:
        current_value = control.value if control.value is not None else control.default
        if control.kind == "bool":
            variable = tk.BooleanVar(value=bool(current_value))
            return variable, ttk.Checkbutton(self.controls_container, variable=variable)

        if control.kind == "menu":
            variable = tk.StringVar()
            options = []
            current_label = None
            for key, value in sorted(control.menu_items.items()):
                label = f"{key}: {value}"
                options.append(label)
                if key == current_value:
                    current_label = label
            if options:
                variable.set(current_label or options[0])
            widget = ttk.Combobox(
                self.controls_container,
                textvariable=variable,
                values=options,
                state="readonly",
            )
            return variable, widget

        step = control.step or 1
        min_value = control.min_value if control.min_value is not None else -999999
        max_value = control.max_value if control.max_value is not None else 999999
        variable = tk.IntVar(value=current_value if current_value is not None else min_value)
        widget = ttk.Spinbox(
            self.controls_container,
            textvariable=variable,
            from_=min_value,
            to=max_value,
            increment=step,
        )
        return variable, widget

    def _format_control_label(self, control: ControlInfo) -> str:
        chunks = [control.name, f"({control.kind})"]
        if control.min_value is not None and control.max_value is not None:
            chunks.append(f"[{control.min_value}..{control.max_value}]")
        if control.step is not None:
            chunks.append(f"step={control.step}")
        return " ".join(chunks)

    def _collect_controls(self) -> dict[str, int]:
        values: dict[str, int] = {}
        for name, control in self.capabilities.controls.items():
            if "inactive" in control.flags or name not in self.control_vars:
                continue
            variable = self.control_vars[name]
            if control.kind == "bool":
                values[name] = int(bool(variable.get()))
            elif control.kind == "menu":
                values[name] = int(str(variable.get()).split(":", 1)[0].strip())
            else:
                values[name] = int(variable.get())
        return values

    def _build_runtime_config(self) -> SessionConfig:
        zoom = float(self.zoom_var.get())
        if zoom <= 0:
            raise ValueError("Preview zoom must be greater than 0.")
        return SessionConfig(
            device_index=self.config.device_index,
            device_path=self.config.device_path,
            pixel_format=self.config.pixel_format,
            resolution=self.config.resolution,
            save_directory=self.save_dir_var.get().strip() or self.config.save_directory,
            file_prefix=self.prefix_var.get().strip() or "capture",
            image_extension=self.extension_var.get(),
            initial_zoom=zoom,
            preview_width=self.config.preview_width,
            preview_height=self.config.preview_height,
            raw_processing_enabled=bool(self.raw_processing_var.get()),
            headless_capture_count=self.config.headless_capture_count,
            headless_interval_seconds=self.config.headless_interval_seconds,
            headless_warmup_frames=self.config.headless_warmup_frames,
            controls=self._collect_controls(),
        )

    def _populate_from_config(self, config: SessionConfig) -> None:
        self.save_dir_var.set(config.save_directory)
        self.prefix_var.set(config.file_prefix)
        self.extension_var.set(config.image_extension)
        self.zoom_var.set(str(config.initial_zoom))
        self.raw_processing_var.set(config.raw_processing_enabled)
        controls = config.controls or {}
        for name, value in controls.items():
            control = self.capabilities.controls.get(name)
            variable = self.control_vars.get(name)
            if control is None or variable is None:
                continue
            if control.kind == "bool":
                variable.set(bool(value))
            elif control.kind == "menu":
                label = control.menu_items.get(int(value))
                if label is not None:
                    variable.set(f"{int(value)}: {label}")
            else:
                variable.set(int(value))

    def process_events(self) -> None:
        if self.closed:
            return
        try:
            self.root.update_idletasks()
            self.root.update()
        except tk.TclError:
            self.closed = True

    def current_zoom(self) -> float:
        try:
            zoom = float(self.zoom_var.get())
        except ValueError:
            return self.config.initial_zoom
        return max(zoom, 0.1)

    def set_zoom(self, value: float) -> None:
        if not self.closed:
            self.zoom_var.set(f"{max(value, 0.1):.2f}")
        self.config.initial_zoom = max(value, 0.1)

    def apply_current_settings(self) -> None:
        try:
            updated = self._build_runtime_config()
            apply_controls(updated.device_path, updated.controls or {})
            self.config.save_directory = updated.save_directory
            self.config.file_prefix = updated.file_prefix
            self.config.image_extension = updated.image_extension
            self.config.initial_zoom = updated.initial_zoom
            self.config.raw_processing_enabled = updated.raw_processing_enabled
            self.config.controls = updated.controls
            self.on_config_changed(self.config)
            self.status_var.set("Applied runtime settings")
        except Exception as exc:
            messagebox.showerror("Apply failed", str(exc))
            self.status_var.set(f"Apply failed: {exc}")

    def save_config_dialog(self) -> None:
        try:
            config = self._build_runtime_config()
        except Exception as exc:
            messagebox.showerror("Invalid configuration", str(exc))
            return
        save_path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile="camera_session.json",
        )
        if not save_path:
            return
        Path(save_path).write_text(config.to_json(), encoding="utf-8")
        self.status_var.set(f"Saved config to {save_path}")

    def load_config_dialog(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            initialdir=str(Path.cwd()),
        )
        if not path:
            return
        try:
            loaded = _load_cli_config(path)
            if loaded.device_path != self.config.device_path or loaded.device_index != self.config.device_index:
                raise ValueError("Runtime load cannot switch to a different camera device.")
            if loaded.pixel_format != self.config.pixel_format:
                raise ValueError("Runtime load cannot change pixel format during an active preview.")
            if tuple(loaded.resolution) != tuple(self.config.resolution):
                raise ValueError("Runtime load cannot change resolution during an active preview.")
            self._populate_from_config(loaded)
            self.apply_current_settings()
            self.status_var.set(f"Loaded config from {path}")
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            self.status_var.set(f"Load failed: {exc}")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.root.destroy()


def run_preview(config: SessionConfig) -> None:
    session_dir = _session_dir(config)
    _save_session_metadata(config, session_dir)

    if config.controls:
        apply_controls(config.device_path, config.controls)

    capture = _open_capture(config)
    runtime_window = RuntimeControlWindow(config, lambda updated: _save_session_metadata(updated, _session_dir(updated)))

    zoom = max(config.initial_zoom, 0.1)
    fullscreen = False
    frame_counter = 0
    saved_images: list[Path] = []
    window_initialized = False

    try:
        while True:
            runtime_window.process_events()
            zoom = runtime_window.current_zoom()
            session_dir = _session_dir(config)

            ok, frame = capture.read()
            if not ok:
                raise RuntimeError("Failed to read a frame from the camera.")
            preview_frame, save_frame = _prepare_frame(frame, config.pixel_format, config.raw_processing_enabled)
            if not window_initialized:
                cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
                cv2.resizeWindow(WINDOW_NAME, config.preview_width, config.preview_height)
                window_initialized = True

            display = _scale_frame(preview_frame, zoom)
            overlay = _add_overlay(
                display,
                zoom,
                preview_frame.shape[1],
                preview_frame.shape[0],
                session_dir,
                len(saved_images),
                config.pixel_format,
                config.raw_processing_enabled,
            )
            cv2.imshow(WINDOW_NAME, overlay)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key in (ord("+"), ord("=")):
                zoom = min(zoom * 1.25, 16.0)
                runtime_window.set_zoom(zoom)
            elif key in (ord("-"), ord("_")):
                zoom = max(zoom / 1.25, 0.1)
                runtime_window.set_zoom(zoom)
            elif key == ord("0"):
                zoom = 1.0
                runtime_window.set_zoom(zoom)
            elif key == ord("c"):
                _save_session_metadata(config, session_dir)
                path = _capture_path(config, session_dir, frame_counter)
                if _write_frame(
                    path,
                    preview_frame,
                    save_frame,
                    config.pixel_format,
                    config.raw_processing_enabled,
                ):
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
        runtime_window.close()
        capture.release()
        cv2.destroyAllWindows()


def run_headless(
    config: SessionConfig,
    capture_count: int | None = None,
    interval_seconds: float | None = None,
    warmup_frames: int | None = None,
) -> list[Path]:
    session_dir = Path(config.save_directory).expanduser().resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    _save_session_metadata(config, session_dir)

    if config.controls:
        apply_controls(config.device_path, config.controls)

    total_captures = capture_count if capture_count is not None else config.headless_capture_count
    delay = interval_seconds if interval_seconds is not None else config.headless_interval_seconds
    warmup = warmup_frames if warmup_frames is not None else config.headless_warmup_frames

    if total_captures < 1:
        raise ValueError("Headless capture count must be at least 1.")
    if delay < 0:
        raise ValueError("Headless capture interval cannot be negative.")
    if warmup < 0:
        raise ValueError("Headless warmup frames cannot be negative.")

    capture = _open_capture(config)
    saved_images: list[Path] = []

    try:
        for _ in range(warmup):
            ok, _frame = capture.read()
            if not ok:
                raise RuntimeError("Failed to read a warmup frame from the camera.")

        for frame_counter in range(total_captures):
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError("Failed to read a frame from the camera.")
            preview_frame, save_frame = _prepare_frame(frame, config.pixel_format, config.raw_processing_enabled)

            path = _capture_path(config, session_dir, frame_counter)
            if not _write_frame(
                path,
                preview_frame,
                save_frame,
                config.pixel_format,
                config.raw_processing_enabled,
            ):
                raise RuntimeError(f"Failed to save image to {path}.")
            saved_images.append(path)
            print(f"Saved image: {path}")

            if frame_counter + 1 < total_captures and delay > 0:
                time.sleep(delay)
    finally:
        capture.release()

    return saved_images


def _scale_frame(frame, zoom: float):
    if abs(zoom - 1.0) < 1e-6:
        return frame
    height, width = frame.shape[:2]
    new_width = max(int(width * zoom), 1)
    new_height = max(int(height * zoom), 1)
    return cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_LINEAR)


def _add_overlay(
    frame,
    zoom: float,
    width: int,
    height: int,
    output_dir: Path,
    save_count: int,
    pixel_format: str,
    raw_processing_enabled: bool,
):
    overlay = frame.copy()
    lines = [
        f"Resolution: {width}x{height}",
        f"Format: {pixel_format}",
        f"Processing: {_processing_label(pixel_format, raw_processing_enabled)}",
        f"Zoom: {zoom:.2f}x",
        f"Saved: {save_count}",
        f"Folder: {output_dir}",
        "Keys: c capture | d delete last | +/- zoom | 0 reset | f fullscreen | h help | q quit",
        "Runtime controls window: adjust live parameters and save/load configs",
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
        raw_processing_enabled=bool(data.get("raw_processing_enabled", True)),
        headless_capture_count=int(data.get("headless_capture_count", 1)),
        headless_interval_seconds=float(data.get("headless_interval_seconds", 0.0)),
        headless_warmup_frames=int(data.get("headless_warmup_frames", 5)),
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
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without the setup or preview windows and capture frames directly from the config.",
    )
    parser.add_argument(
        "--count",
        type=int,
        help="Optional number of frames to capture in headless mode. Overrides the config value.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        help="Optional delay in seconds between captures in headless mode. Overrides the config value.",
    )
    parser.add_argument(
        "--warmup-frames",
        type=int,
        help="Optional number of frames to discard before saving in headless mode. Overrides the config value.",
    )
    args = parser.parse_args(argv)

    try:
        if args.headless and not args.config:
            raise ValueError("--headless requires --config.")
        if args.config and (args.run_config_direct or args.headless):
            config = _load_cli_config(args.config)
        else:
            window = ConfigWindow(config_path=args.config)
            config = window.run()
        if args.headless:
            run_headless(
                config,
                capture_count=args.count,
                interval_seconds=args.interval,
                warmup_frames=args.warmup_frames,
            )
        else:
            _print_shortcuts()
            run_preview(config)
        return 0
    except (V4L2Error, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1

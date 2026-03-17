from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from .v4l2 import CameraCapabilities, CameraDevice, ControlInfo, get_capabilities, list_devices
except ImportError:
    from v4l2 import CameraCapabilities, CameraDevice, ControlInfo, get_capabilities, list_devices


@dataclass(slots=True)
class SessionConfig:
    device_index: int
    device_path: str
    pixel_format: str
    resolution: tuple[int, int]
    save_directory: str
    file_prefix: str = "capture"
    image_extension: str = "png"
    initial_zoom: float = 1.0
    preview_width: int = 1280
    preview_height: int = 720
    raw_processing_enabled: bool = True
    headless_capture_count: int = 1
    headless_interval_seconds: float = 0.0
    headless_warmup_frames: int = 5
    controls: dict[str, int] | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


class ConfigWindow:
    def __init__(self, config_path: str | None = None) -> None:
        self.root = tk.Tk()
        self.root.title("Camera Controller Setup")
        self.root.geometry("920x760")
        self.root.minsize(760, 640)

        self.selected_config: SessionConfig | None = None
        self.devices: list[CameraDevice] = []
        self.capabilities: CameraCapabilities | None = None
        self.control_vars: dict[str, tk.Variable] = {}
        self.control_widgets: list[tk.Widget] = []
        self.pending_import_data: dict | None = None

        self.device_var = tk.StringVar()
        self.format_var = tk.StringVar()
        self.resolution_var = tk.StringVar()
        self.save_dir_var = tk.StringVar(value=str(Path.cwd() / "captures"))
        self.prefix_var = tk.StringVar(value="capture")
        self.extension_var = tk.StringVar(value="png")
        self.zoom_var = tk.StringVar(value="1.0")
        self.preview_width_var = tk.StringVar(value="1280")
        self.preview_height_var = tk.StringVar(value="720")
        self.raw_processing_var = tk.BooleanVar(value=True)

        self._build_layout()
        self._load_devices()
        if config_path:
            self._load_json_defaults(config_path)

    def run(self) -> SessionConfig:
        self.root.mainloop()
        if self.selected_config is None:
            raise SystemExit(1)
        return self.selected_config

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        top = ttk.Frame(self.root, padding=12)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)

        ttk.Label(top, text="Camera").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=6)
        self.device_combo = ttk.Combobox(top, textvariable=self.device_var, state="readonly")
        self.device_combo.grid(row=0, column=1, sticky="ew", pady=6)
        self.device_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_device_changed())

        ttk.Button(top, text="Refresh", command=self._load_devices).grid(row=0, column=2, padx=8, pady=6)

        ttk.Label(top, text="Pixel Format").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=6)
        self.format_combo = ttk.Combobox(top, textvariable=self.format_var, state="readonly")
        self.format_combo.grid(row=1, column=1, sticky="ew", pady=6)
        self.format_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_format_changed())

        ttk.Label(top, text="Resolution").grid(row=1, column=2, sticky="w", padx=(8, 8), pady=6)
        self.resolution_combo = ttk.Combobox(top, textvariable=self.resolution_var, state="readonly")
        self.resolution_combo.grid(row=1, column=3, sticky="ew", pady=6)

        ttk.Label(top, text="Save Folder").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=6)
        save_entry = ttk.Entry(top, textvariable=self.save_dir_var)
        save_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=6)
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

        ttk.Label(top, text="Initial Zoom").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(top, textvariable=self.zoom_var).grid(row=4, column=1, sticky="ew", pady=6)

        ttk.Label(top, text="Preview Size").grid(row=4, column=2, sticky="w", padx=(8, 8), pady=6)
        preview_size = ttk.Frame(top)
        preview_size.grid(row=4, column=3, sticky="ew", pady=6)
        preview_size.columnconfigure(0, weight=1)
        preview_size.columnconfigure(1, weight=1)
        ttk.Entry(preview_size, textvariable=self.preview_width_var, width=10).grid(row=0, column=0, sticky="ew")
        ttk.Entry(preview_size, textvariable=self.preview_height_var, width=10).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        self.raw_processing_check = ttk.Checkbutton(
            top,
            text="Basic raw processing (normalize + demosaic)",
            variable=self.raw_processing_var,
        )
        self.raw_processing_check.grid(row=5, column=0, columnspan=4, sticky="w", pady=6)

        controls_frame = ttk.LabelFrame(self.root, text="Camera Controls", padding=8)
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

        bottom = ttk.Frame(self.root, padding=(12, 0, 12, 12))
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        actions = ttk.Frame(bottom)
        actions.grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="Import Config", command=self._import_config).grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="Export Config", command=self._export_config).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(bottom, text="Quit", command=self.root.destroy).grid(row=0, column=1, padx=8)
        ttk.Button(bottom, text="Start Preview", command=self._submit).grid(row=0, column=2)

    def _on_controls_configure(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_resize(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

    def _pick_directory(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.save_dir_var.get() or str(Path.cwd()))
        if selected:
            self.save_dir_var.set(selected)

    def _load_devices(self) -> None:
        try:
            self.devices = list_devices()
        except Exception as exc:
            messagebox.showerror("Camera discovery failed", str(exc))
            self.devices = []
        labels = [f"{device.index}: {device.name} ({device.path})" for device in self.devices]
        self.device_combo["values"] = labels
        if labels:
            current = self.device_var.get()
            if current not in labels:
                self.device_var.set(labels[0])
            self._on_device_changed()

    def _on_device_changed(self) -> None:
        device = self._selected_device()
        if not device:
            return
        try:
            self.capabilities = get_capabilities(device.path)
        except Exception as exc:
            messagebox.showerror("Capability discovery failed", str(exc))
            self.capabilities = None
            return
        formats = sorted(self.capabilities.formats.keys())
        self.format_combo["values"] = formats
        if formats:
            if self.format_var.get() not in formats:
                preferred = "MJPG" if "MJPG" in formats else formats[0]
                self.format_var.set(preferred)
            self._on_format_changed()
        self._render_controls()
        self._apply_pending_import()

    def _on_format_changed(self) -> None:
        if not self.capabilities:
            return
        resolutions = self.capabilities.formats.get(self.format_var.get(), [])
        labels = [f"{width}x{height}" for width, height in resolutions]
        self.resolution_combo["values"] = labels
        if labels and self.resolution_var.get() not in labels:
            self.resolution_var.set(labels[0])
        self._sync_processing_toggle()

    def _selected_device(self) -> CameraDevice | None:
        if not self.devices:
            return None
        current = self.device_var.get()
        for device in self.devices:
            label = f"{device.index}: {device.name} ({device.path})"
            if label == current:
                return device
        return self.devices[0]

    def _sync_processing_toggle(self) -> None:
        is_raw = self.format_var.get().startswith(("BA", "BG", "GB", "GR", "RG")) or self.format_var.get() == "BA81"
        state = "normal" if is_raw else "disabled"
        self.raw_processing_check.configure(state=state)
        if not is_raw:
            self.raw_processing_var.set(True)

    def _render_controls(self) -> None:
        for widget in self.control_widgets:
            widget.destroy()
        self.control_widgets.clear()
        self.control_vars.clear()
        if not self.capabilities:
            return

        row = 0
        for name, control in sorted(self.capabilities.controls.items()):
            if "inactive" in control.flags:
                continue
            label = ttk.Label(self.controls_container, text=self._format_control_label(control))
            label.grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            self.control_widgets.append(label)

            variable, widget = self._build_control_widget(control)
            widget.grid(row=row, column=1, sticky="ew", pady=4)
            self.controls_container.columnconfigure(1, weight=1)
            self.control_vars[name] = variable
            self.control_widgets.append(widget)
            row += 1

    def _build_control_widget(self, control: ControlInfo) -> tuple[tk.Variable, tk.Widget]:
        current_value = control.value if control.value is not None else control.default
        if control.kind == "bool":
            variable = tk.BooleanVar(value=bool(current_value))
            return variable, ttk.Checkbutton(self.controls_container, variable=variable)

        if control.kind == "menu":
            variable = tk.StringVar()
            menu_options = []
            current_label = None
            for key, value in sorted(control.menu_items.items()):
                option = f"{key}: {value}"
                menu_options.append(option)
                if key == current_value:
                    current_label = option
            if menu_options:
                variable.set(current_label or menu_options[0])
            widget = ttk.Combobox(
                self.controls_container,
                textvariable=variable,
                values=menu_options,
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
        collected: dict[str, int] = {}
        if not self.capabilities:
            return collected
        for name, control in self.capabilities.controls.items():
            if name not in self.control_vars or "inactive" in control.flags:
                continue
            variable = self.control_vars[name]
            if control.kind == "bool":
                collected[name] = int(bool(variable.get()))
            elif control.kind == "menu":
                raw = str(variable.get()).split(":", 1)[0].strip()
                collected[name] = int(raw)
            else:
                collected[name] = int(variable.get())
        return collected

    def _import_config(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            initialdir=str(Path.cwd()),
        )
        if not path:
            return
        try:
            self._load_json_defaults(path)
        except Exception as exc:
            messagebox.showerror("Import failed", str(exc))

    def _apply_pending_import(self) -> None:
        if not self.pending_import_data:
            return

        data = self.pending_import_data

        device_path = data.get("device_path")
        device_index = data.get("device_index")
        target_device = None
        for device in self.devices:
            if device_path and device.path == device_path:
                target_device = device
                break
            if device_index is not None and device.index == int(device_index):
                target_device = device
                break

        if target_device:
            target_label = f"{target_device.index}: {target_device.name} ({target_device.path})"
            if self.device_var.get() != target_label:
                self.device_var.set(target_label)
                self._on_device_changed()
                return

        pixel_format = data.get("pixel_format")
        if pixel_format and self.capabilities and pixel_format in self.capabilities.formats:
            self.format_var.set(pixel_format)
            self._on_format_changed()

        resolution = data.get("resolution")
        if resolution and len(resolution) == 2:
            resolution_label = f"{int(resolution[0])}x{int(resolution[1])}"
            if resolution_label in self.resolution_combo.cget("values"):
                self.resolution_var.set(resolution_label)

        controls = data.get("controls", {})
        for name, value in controls.items():
            variable = self.control_vars.get(name)
            control = self.capabilities.controls.get(name) if self.capabilities else None
            if variable is None or control is None:
                continue
            if control.kind == "bool":
                variable.set(bool(value))
            elif control.kind == "menu":
                label = control.menu_items.get(int(value))
                if label is not None:
                    variable.set(f"{int(value)}: {label}")
            else:
                variable.set(int(value))

        self.pending_import_data = None

    def _build_session_config(self) -> SessionConfig:
        device = self._selected_device()
        if not device:
            raise ValueError("No camera device is available.")
        if not self.format_var.get():
            raise ValueError("Choose a pixel format.")
        if "x" not in self.resolution_var.get():
            raise ValueError("Choose a resolution.")
        width_text, height_text = self.resolution_var.get().split("x", 1)
        return SessionConfig(
            device_index=device.index,
            device_path=device.path,
            pixel_format=self.format_var.get(),
            resolution=(int(width_text), int(height_text)),
            save_directory=self.save_dir_var.get(),
            file_prefix=self.prefix_var.get().strip() or "capture",
            image_extension=self.extension_var.get(),
            initial_zoom=float(self.zoom_var.get()),
            preview_width=int(self.preview_width_var.get()),
            preview_height=int(self.preview_height_var.get()),
            raw_processing_enabled=bool(self.raw_processing_var.get()),
            headless_capture_count=1,
            headless_interval_seconds=0.0,
            headless_warmup_frames=5,
            controls=self._collect_controls(),
        )

    def _submit(self) -> None:
        try:
            config = self._build_session_config()
        except Exception as exc:
            messagebox.showerror("Invalid configuration", str(exc))
            return
        self.selected_config = config
        self.root.destroy()

    def _export_config(self) -> None:
        try:
            config = self._build_session_config()
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

    def _load_json_defaults(self, config_path: str) -> None:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        self.pending_import_data = data
        self.save_dir_var.set(data.get("save_directory", self.save_dir_var.get()))
        self.prefix_var.set(data.get("file_prefix", self.prefix_var.get()))
        self.extension_var.set(data.get("image_extension", self.extension_var.get()))
        self.zoom_var.set(str(data.get("initial_zoom", self.zoom_var.get())))
        self.preview_width_var.set(str(data.get("preview_width", self.preview_width_var.get())))
        self.preview_height_var.set(str(data.get("preview_height", self.preview_height_var.get())))
        self.raw_processing_var.set(bool(data.get("raw_processing_enabled", True)))
        self._apply_pending_import()

# Camera Controller

`camera_controller` is a Ubuntu-first camera capture tool。

- a pre-start setup window
- support for changing camera format, resolution, exposure, brightness, and other V4L2 controls before streaming
- save-folder selection
- a live preview window with keyboard shortcuts
- a terminal-friendly launch command

It targets UVC / V4L2 cameras on Linux and uses:

- `v4l2-ctl` for device discovery, supported formats, and tunable controls
- OpenCV for camera streaming, preview, and image saving
- Tkinter for the setup interface

## 1. Requirements

Install system packages first:

```bash
sudo apt install v4l-utils python3-tk
```

Install Python dependencies from the project root:

```bash
python3 -m pip install -e . --no-build-isolation
```

If you do not want editable install mode:

```bash
python3 -m pip install . --no-build-isolation
```

## 2. Launch

From the terminal:

```bash
camera-controller
```

You can also run it without installing the script:

```bash
python3 .
```

## 3. Setup Window Workflow

When the app starts, it opens a configuration window before the camera stream begins.

Use that window to configure:

- `Camera`: choose a detected `/dev/video*` device
- `Pixel Format`: choose a camera-supported format such as `MJPG` or `YUYV`
- `Resolution`: choose one of the supported resolutions for the selected format
- `Save Folder`: select where captured images will be written
- `Prefix`: filename prefix for saved images
- `Extension`: output image type such as `png` or `jpg`
- `Initial Zoom`: preview zoom multiplier used when the stream opens
- `Preview Size`: initial preview window size in pixels
- `Camera Controls`: all active controls reported by `v4l2-ctl --list-ctrls-menus`

### Control Types

The setup panel maps V4L2 controls to widgets automatically:

- integer controls: spin boxes
- boolean controls: checkboxes
- menu controls: dropdown menus

That means you can set values like:

- exposure
- brightness
- contrast
- saturation
- gain
- white balance
- sharpness
- backlight compensation
- any other tunable control that your camera exposes through V4L2

After the configuration is ready, click `Start Preview`.

## 4. Preview Window

The preview opens in a resizable OpenCV window named `Camera Preview`.

At the same time, a second Tk window named `Runtime Controls` opens. Use it during streaming to:

- change live V4L2 control values
- update save folder, filename prefix, image extension, and preview zoom
- apply changes without restarting the preview
- save the current runtime state to a JSON config
- load a saved config back into the running session

Runtime config loads keep the current device, pixel format, and resolution fixed for the active stream. If those fields differ in the JSON file, the app rejects the load instead of trying to re-open the camera mid-session.

You can:

- drag the window borders to resize it
- use fullscreen mode
- zoom the displayed image in and out
- capture frames while the stream is running
- delete the last saved image

The app writes `session_config.json` into the selected output directory so each capture session records the settings that were used.

## 5. Keyboard Shortcuts

Available in the preview window:

- `c`: capture the current frame
- `d`: delete the previously captured image
- `+` or `=`: zoom in
- `-` or `_`: zoom out
- `0`: reset zoom to `1.0x`
- `f`: toggle fullscreen
- `h`: print shortcut help in the terminal
- `q` or `Esc`: quit

## 6. Output Files

Captured images are saved in the selected folder using this pattern:

```text
<prefix>_<counter>_<timestamp>.<extension>
```

Example:

```text
capture_0003_20260312_164500.png
```

The same folder also gets:

```text
session_config.json
```

This file stores:

- device index and path
- chosen pixel format
- chosen resolution
- preview size
- initial zoom
- save directory
- all configured camera control values

## 7. Export and Reuse Configurations

The setup window has an `Export Config` button. Use it to save a reusable JSON file.

### Reopen the setup window with a previous config preloaded

```bash
camera-controller --config my_session.json
```

### Run a saved config directly without showing the setup window

```bash
camera-controller --config my_session.json --run-config-direct
```

### Run headless from a saved config

This skips both the Tk setup window and the OpenCV preview window and saves frames directly to the configured output folder.

```bash
camera-controller --config my_session.json --headless
```

Optional overrides for headless mode:

```bash
camera-controller --config my_session.json --headless --count 10 --interval 0.5 --warmup-frames 10
```

Supported JSON fields for headless defaults:

- `headless_capture_count`
- `headless_interval_seconds`
- `headless_warmup_frames`

## 8. Typical Usage

Example workflow:

1. Start the tool from the terminal.
2. Pick the correct camera device.
3. Choose the format and resolution you want.
4. Set your save folder.
5. Adjust exposure, brightness, and any other controls in the setup window.
6. Click `Start Preview`.
7. Use `+` and `-` to change preview zoom.
8. Press `c` to save images.
9. Press `d` if you want to remove the most recent capture.
10. Press `q` when finished.

Headless example:

1. Export a config from the setup window or create a JSON config manually.
2. Run `camera-controller --config my_session.json --headless`.
3. Check the configured save folder for the captured images and `session_config.json`.

## 9. Troubleshooting

### `v4l2-ctl was not found`

Install:

```bash
sudo apt install v4l-utils
```

### Tkinter window does not open

Install:

```bash
sudo apt install python3-tk
```

### Camera opens but ignores some controls

Some webcams expose controls but do not honor every value through a given driver or format. Try:

- a different pixel format such as `MJPG` vs `YUYV`
- a different resolution
- checking supported values with `v4l2-ctl --device /dev/videoX --list-ctrls-menus`

### Permission denied for `/dev/video*`

Make sure your user has camera access, for example through the `video` group.

## 10. Developer Notes

Main files in the project root:

- `v4l2.py`: V4L2 discovery and control application
- `config_ui.py`: setup window and config export
- `controller.py`: preview loop, capture flow, keyboard handling
- `__main__.py`: `python -m camera_controller` entrypoint

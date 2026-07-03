# Optocam Zero Buildroot firmware

Current fast-boot firmware source for Optocam Zero. The older Raspberry Pi OS /
Python version is kept in [`../python-legacy/`](../python-legacy/).

## Contents

- `defconfig/` - Buildroot defconfig for the current firmware image.
- `src/` - native Optocam app sources plus its `Makefile`.
- `package/optocamzero/` - Buildroot package that cross-compiles and installs
  the native app (`optocam_app` and the `optocam_preview` fallback) to
  `/usr/bin`.
- `config/` - Raspberry Pi boot, genimage, BusyBox, and board scripts.
- `kernel/` - kernel config fragments.
- `overlay/` - root filesystem overlay.
- `debug/` - optional bring-up scripts.

## Build

These files drop into a Buildroot checkout. The defconfig references in-tree
paths, so copy each file to the location below:

| This repo | Buildroot checkout |
| --- | --- |
| `defconfig/optocam_zero_buildroot_defconfig` | `configs/` |
| `package/optocamzero/` | `package/optocamzero/` |
| `src/` (incl. `Makefile`) | `package/optocamzero/src/` |
| `overlay/` | `board/raspberrypi/overlay/` |
| `kernel/*.fragment` | `board/raspberrypi/` |
| `config/busybox.fragment` | `board/raspberrypi/` |
| `config/post-build.sh`, `config/post-image.sh` | `board/raspberrypizero2w/` |
| `config/config_zero2w.txt`, `config/cmdline.txt`, `config/genimage.cfg.in` | `board/raspberrypizero2w/` |

Register the package by adding one line to Buildroot's `package/Config.in`
(e.g. under the "Hardware handling" or "Miscellaneous" menu):

```
source "package/optocamzero/Config.in"
```

Then build:

```sh
make optocam_zero_buildroot_defconfig
make
```

This produces `output/images/sdcard.img`. The `optocamzero` package compiles the
native app against the target's libcamera, libjpeg, and freetype and installs
`/usr/bin/optocam_app` and `/usr/bin/optocam_preview`.

## Runtime Defaults

- Target: Raspberry Pi Zero 2 W + IMX708 camera.
- Photos: `/data/photos`.
- Transfer mode: `Optocam Zero`, `192.168.4.1`, password `0026opto`.

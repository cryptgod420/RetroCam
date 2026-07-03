# Optocam Zero Buildroot firmware

Current fast-boot firmware source for Optocam Zero. The older Raspberry Pi OS /
Python version is kept in `../python-legacy/`.

## Contents

- `defconfig/` - Buildroot defconfig for the current firmware image.
- `src/` - native Optocam app sources.
- `config/` - Raspberry Pi boot, genimage, BusyBox, and board scripts.
- `kernel/` - kernel config fragments.
- `overlay/` - root filesystem overlay.
- `debug/` - optional bring-up scripts.

## Build

Copy these files into a Buildroot checkout, matching the paths implied by the
defconfig, then run:

```sh
make optocam_zero_buildroot_defconfig
make
```

## Runtime Defaults

- Target: Raspberry Pi Zero 2 W + IMX708 camera.
- Photos: `/data/photos`.
- Transfer mode: `Optocam Zero`, `192.168.4.1`, password `0026opto`.
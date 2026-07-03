# Release notes - 2026-07-03 Buildroot firmware bundle

This bundle packages the current Optocam Zero Buildroot port for publication.
It keeps the earlier Python/Raspberry Pi OS software in the existing GitHub repo
and adds the Buildroot firmware as a separate folder.

## Included

- Native `optocam_app.cpp` firmware app for fast boot and camera preview.
- Buildroot defconfig generated from the current working container.
- Raspberry Pi Zero 2 W boot config and kernel command line.
- Rootfs overlay with init scripts, read-only root handling, transfer-mode web
  gallery, hotspot scripts, splash/font assets, WiFi setup, and SSH setup.
- Kernel fragments for the lean Optocam boot profile.
- Diagnostic scripts in `debug/` for bring-up.

## Not included

- Generated Buildroot `output/` or `dl/`.
- Cross toolchain caches.- Device photos, logs, crash dumps, SSH host keys, WiFi credentials, or local
  machine paths.

## Current device/development notes

- Current deployed device binary on July 3 was md5
  `4a20f825de0e37a8e8aeca6220e376d6`.
- The app includes SIGTERM forensics that log to `/data/sigcatch.log` and
  survive non-init SIGTERM/HUP/INT/QUIT/PIPE.
- Because of that SIGTERM handler, development deploys must restart the app
  with `killall -9 optocam_app`, not plain `killall optocam_app`.
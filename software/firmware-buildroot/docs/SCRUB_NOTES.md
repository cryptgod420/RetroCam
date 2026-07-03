# Scrub notes

The release bundle was prepared from the current Buildroot working tree and
checked for common private material before copying into the GitHub repo.

## Removed or excluded

- Build outputs and caches: `output/`, `dl/`, toolchains, generated images.
- Device state: photos, logs, power journals, crash logs, core dumps.
- SSH material: private keys, host keys, authorized keys, known hosts.
- Local editor files: `.DS_Store`, `.claude/`, IDE folders.
- Old internal `PORT-SPEC.md` deploy command with a private LAN IP was not
  copied into the public bundle.

## Left intentionally

- `192.168.4.1`, because it is the product hotspot address shown to users.
- `Optocam Zero` AP name and `0026opto` AP passphrase, because the current
  transfer-mode screen displays this default and the firmware needs it to match.
- `/home/dkumkum` paths, because the gallery server and overlay currently use
  that on-device account/path as part of the firmware layout.
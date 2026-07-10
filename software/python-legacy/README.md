> ⚠️ 
> This is the original Raspberry Pi OS / Python version. It still works, but it's no longer the recommended install (slower boot, not updated). For the current firmware installation, see the [software README](../README.md).

<br>

## Requirements

- Completed RETROCAM with the correct components.
- Micro sd card (16GB or larger, A2 type recommended for best performance).
- A computer with internet access.

<br>

## Installation

**1. Flash the SD card**

Download Raspberry Pi Imager from [raspberrypi.com/software](https://www.raspberrypi.com/software/) and install it. Select **Raspberry Pi Zero 2W** as the device and **Raspberry Pi OS Lite (32-bit) Bookworm** as the OS. You can find it under Raspberry Pi OS (other).

Before flashing, click **Edit Settings** and fill in your hostname, username, password, and WiFi credentials (remember to take note of this info). Go to the Services tab and enable SSH. Click Save, then flash the card.

**2. First boot**

Insert the SD card into the Pi and power it on. Wait about 1-2 minutes for it to boot and connect to your WiFi.

**3. Connect via SSH**

Open Terminal on your computer and run:

```
ssh your-username@your-hostname.local
```

Type `yes` when asked about the fingerprint, then enter your password.

**4. Run the installer**

```
sudo apt-get update
```

```
sudo apt-get install -y git
```

```
git clone https://github.com/dorukkumkumoglu/optocamzero.git && sudo bash optocamzero/software/python-legacy/install.sh
```

Installation takes about 10-15 minutes. The Pi reboots automatically when done and the camera starts immediately.

<br>

## Troubleshooting

**SSH shows "host key changed" error:**
```
ssh-keygen -R your-hostname.local
```

**Camera does not start after reboot:**
```
sudo systemctl status camera-auto.service
```

<br>




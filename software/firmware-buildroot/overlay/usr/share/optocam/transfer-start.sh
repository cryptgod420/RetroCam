#!/bin/sh
# Enter transfer mode: station -> AP "Optocam Zero" @192.168.4.1 + web gallery.
# Everything here is OFF the boot path — runs only on joystick long-press.
exec >> /data/transfer.log 2>&1
echo "=== transfer-start $(cat /proc/uptime) ==="
set -x
killall wpa_supplicant udhcpc 2>/dev/null

# --- idempotency: tear down any stale transfer services from a prior entry so a
# second long-press never stacks a duplicate hostapd/dnsmasq/gallery server (the
# duplicate python can't even bind :80). Kill by pidfile, but only if the PID is
# still the process we expect — guards against PID reuse killing something else. ---
kill_if() {   # kill_if <pidfile> <comm-substring>
  p=$(cat "$1" 2>/dev/null)
  case "$p" in ''|*[!0-9]*) rm -f "$1"; return;; esac
  if grep -qs "$2" "/proc/$p/comm" 2>/dev/null; then kill "$p" 2>/dev/null; fi
  rm -f "$1"
}
kill_if /run/gallery-server.pid   python
kill_if /run/dnsmasq-transfer.pid dnsmasq
killall hostapd 2>/dev/null     # hostapd only ever runs during transfer
sleep 0.3

modprobe brcmfmac 2>/dev/null                 # no-op if already up
i=0; while [ ! -d /sys/class/net/wlan0 ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i+1)); done
ifconfig wlan0 down 2>/dev/null
ifconfig wlan0 192.168.4.1 netmask 255.255.255.0 up
hostapd -B /usr/share/optocam/hostapd.conf
dnsmasq --interface=wlan0 --bind-interfaces \
        --dhcp-range=192.168.4.10,192.168.4.50,12h \
        --pid-file=/run/dnsmasq-transfer.pid 2>/dev/null
mkdir -p /data/photos 2>/dev/null
python3 /usr/share/optocam/gallery_server.py >> /data/gallery.log 2>&1 &
echo $! > /run/gallery-server.pid

#!/usr/bin/env python3
import sys
import time
_script_start = time.time()
def log(msg):
    sys.stderr.write(f"[{time.time() - _script_start:.2f}s] {msg}\n")
    sys.stderr.flush()
log("Script file started")

# ── Lazy-av shim ──────────────────────────────────────────────────────────────
import types as _types
import importlib as _importlib
class _LazyAv(_types.ModuleType):
    def __getattr__(self, name):
        del sys.modules['av']                       
        real = _importlib.import_module('av')       
        return getattr(real, name)                  
if 'av' not in sys.modules:
    sys.modules['av'] = _LazyAv('av')
log("lazy-av shim installed")
# ──────────────────────────────────────────────────────────────────────────────

import RPi.GPIO as GPIO
log("GPIO imported")
import pigpio
log("pigpio imported")
import spidev
log("spidev imported")
import threading
log("threading imported")

Image = None  
def _preload_heavy():
    global Image
    import picamera2  
    from PIL import Image as _Image
    Image = _Image
_preload_thread = threading.Thread(target=_preload_heavy, daemon=True)
_preload_thread.start()
log("heavy-preload thread started (picamera2 + PIL)")

import os
log("os imported")
import numpy as np
log("numpy imported")
import gc
import subprocess
log("All imports done")

def _wait_for_path(path, timeout=30):
    end = time.time() + timeout
    while not os.path.exists(path):
        if time.time() > end:
            log(f"WARN: timed out waiting for {path}")
            return False
        time.sleep(0.02)
    return True

def connect_pigpiod(timeout=30):
    end = time.time() + timeout
    while True:
        pi = pigpio.pi()
        if pi.connected:
            return pi
        pi.stop()
        if time.time() > end:
            log("WARN: pigpiod connect timed out")
            return pi
        time.sleep(0.1)

_wait_for_path('/dev/gpiomem')      
_wait_for_path('/dev/spidev0.0')    
log("hardware nodes ready")

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
for pin in [16, 20, 5, 26, 13, 6, 19]:
    try:
        GPIO.remove_event_detect(pin)
    except:
        pass
GPIO.cleanup()
time.sleep(0.2)

# Pins
RST_PIN = 27
DC_PIN = 25
BL_PIN = 24
BUTTON_CAPTURE = 16
BUTTON_PREVIEW = 20
BUTTON_LEFT = 5
BUTTON_RIGHT = 26
BUTTON_PRESS = 13
BUTTON_UP = 6
BUTTON_DOWN = 19

# Setup GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setup(RST_PIN, GPIO.OUT)
GPIO.setup(DC_PIN, GPIO.OUT)
GPIO.setup(BL_PIN, GPIO.OUT)
_pi = connect_pigpiod()
_pi.set_PWM_frequency(BL_PIN, 1000)
_pi.set_PWM_dutycycle(BL_PIN, 0)

GPIO.setup(BUTTON_PREVIEW, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_CAPTURE, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_LEFT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_RIGHT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_PRESS, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_UP, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_DOWN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# SPI
spi = spidev.SpiDev()
spi.open(0, 0)
spi.max_speed_hz = 31250000
spi.mode = 0
spi.bits_per_word = 8

camera_lock = threading.RLock()
display_lock = threading.Lock()

class CameraConfigCache:
    def __init__(self, picam2):
        self.preview_config = picam2.create_preview_configuration(
            main={"size": (320, 240), "format": "RGB888"},
            buffer_count=3,
            queue=False,
            controls={"FrameDurationLimits": (100, 25000)}
        )
        self._picam2 = picam2
        self.capture_config = None
        self.gif_config = None

    def get_capture_config(self):
        if self.capture_config is None:
            self.capture_config = self._picam2.create_still_configuration(
                main={"size": (2592, 1944), "format": "RGB888"},
                buffer_count=2
            )
        return self.capture_config

    def get_gif_config(self):
        if self.gif_config is None:
            self.gif_config = self._picam2.create_preview_configuration(
                main={"size": (640, 480), "format": "RGB888"},
                buffer_count=4,
                controls={"FrameDurationLimits": (33333, 100000)}
            )
        return self.gif_config

config_cache = None
FONT_PATH = "/home/dkumkum/retrocam.otf"

_shadow_cache = {}
_hud_overlay_cache = {"key": None, "img": None}

def make_text_shadow(text, x, y, font):
    from PIL import ImageDraw, ImageFilter
    shadow = Image.new("RGBA", (320, 240), (0, 0, 0, 0)) 
    ImageDraw.Draw(shadow).text((x, y), text, font=font, fill=(0, 0, 0, 250))
    return shadow.filter(ImageFilter.GaussianBlur(radius=4))

# ── Camera Control Definitions ──
AWB_MODES = [
    (1, "Tungsten",    "TNG"),
    (2, "Fluorescent", "FLR"),
    (3, "Indoor",      "IND"),
    (4, "Daylight",    "DAY"),
    (5, "Cloudy",      "CLD"),
]
EV_VALUES = [-4.0, -3.0, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0]
ISO_VALUES = [0, 100, 200, 400, 800, 1600]
SHUTTER_VALUES = [0, 125000, 66667, 33333, 16667, 8000, 4000, 2000, 1000]
SHUTTER_LABELS = ["S Auto", "1/8", "1/15", "1/30", "1/60", "1/125", "1/250", "1/500", "1/1000"]

_STANDARD_ISO = [100, 125, 160, 200, 250, 320, 400, 500, 640, 800, 1000, 1250, 1600]
_STANDARD_SHUTTERS = [
    (125, "1/8000"), (156, "1/6400"), (200, "1/5000"), (250, "1/4000"),
    (313, "1/3200"), (400, "1/2500"), (500, "1/2000"), (625, "1/1600"),
    (800, "1/1250"), (1000, "1/1000"), (1250, "1/800"), (1563, "1/640"),
    (2000, "1/500"), (2500, "1/400"), (3125, "1/320"), (4000, "1/250"),
    (5000, "1/200"), (6250, "1/160"), (8000, "1/125"), (10000, "1/100"),
    (12500, "1/80"), (16667, "1/60"), (20000, "1/50"), (25000, "1/40"),
    (33333, "1/30"), (40000, "1/25"), (50000, "1/20"), (66667, "1/15"),
    (100000, "1/10"), (125000, "1/8"), (166667, "1/6"), (200000, "1/5"),
    (250000, "1/4"), (333333, "1/3"), (500000, "1/2"), (1000000, "1\""),
]

def nearest_standard_iso(gain):
    iso = gain * 100
    return min(_STANDARD_ISO, key=lambda s: abs(s - iso))

def nearest_standard_shutter(exp_us):
    return min(_STANDARD_SHUTTERS, key=lambda s: abs(s[0] - exp_us))[1]

# Global States
active_setting_index = 0  # 0:AWB, 1:EV, 2:ISO, 3:Shutter
ev_index = 6
iso_index = 0
shutter_index = 0

def get_camera_controls():
    global active_setting_index, ev_index, iso_index, shutter_index, awb_mode_index, filter_index
    ctrls = _FILM_ISP[FILTERS[filter_index]].copy()
    ctrls["AwbMode"] = AWB_MODES[awb_mode_index][0]
    
    is_manual_iso = (ISO_VALUES[iso_index] != 0)
    is_manual_shutter = (SHUTTER_VALUES[shutter_index] != 0)

    if is_manual_iso and is_manual_shutter:
        # STRICT MANUAL PRIORITY: Lock out auto-exposure completely.
        ctrls["AeEnable"] = False
        ctrls["AnalogueGain"] = ISO_VALUES[iso_index] / 100.0
        ctrls["ExposureTime"] = SHUTTER_VALUES[shutter_index]
    else:
        # SEMI-AUTO: EV acts as a dynamic offset target for whatever is left on Auto.
        ctrls["AeEnable"] = True
        ctrls["ExposureValue"] = EV_VALUES[ev_index]
        if is_manual_iso:
            ctrls["AnalogueGain"] = ISO_VALUES[iso_index] / 100.0
        if is_manual_shutter:
            ctrls["ExposureTime"] = SHUTTER_VALUES[shutter_index]
            
    return ctrls

def get_cached_shadow(key, text, x, y, font):
    if _shadow_cache.get(key) != text:
        _shadow_cache[key] = text
        _shadow_cache[key + "_img"] = make_text_shadow(text, x, y, font)
    return _shadow_cache[key + "_img"]

_centre_msg_sprite_cache = {}

def get_centre_msg_sprite(text):
    if text in _centre_msg_sprite_cache:
        return _centre_msg_sprite_cache[text]
    from PIL import ImageDraw, ImageFilter
    font = load_font(25)
    shadow = Image.new("RGBA", (320, 240), (0, 0, 0, 0)) 
    white  = Image.new("RGBA", (320, 240), (0, 0, 0, 0))
    wd = ImageDraw.Draw(white)
    b = wd.textbbox((0, 0), text, font=font)
    mx = (320 - (b[2] - b[0])) // 2 - b[0]
    my = (240 - (b[3] - b[1])) // 2 - b[1] 
    ImageDraw.Draw(shadow).text((mx, my), text, font=font, fill=(0, 0, 0, 250))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=4))
    wd.text((mx, my), text, font=font, fill=(255, 255, 255, 255))
    sprite = Image.alpha_composite(shadow, white)
    _centre_msg_sprite_cache[text] = sprite
    return sprite

def _ease_out_back(t, c1=1.9):
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2

def _ease_centre_msg(age, dur):
    A_IN, A_OUT, S_IN = 0.12, 0.18, 0.18
    if age < A_IN:
        alpha = 1 - (1 - age / A_IN) ** 3
    elif age > dur - A_OUT:
        t = max(0.0, (dur - age) / A_OUT)
        alpha = t * t
    else:
        alpha = 1.0
    if age < S_IN:
        t = age / S_IN
        scale = 0.80 + 0.20 * (1 - (1 - t) ** 3)
    else:
        scale = 1.0
    return alpha, scale

def render_centre_msg(base_rgb, text, age, dur):
    a, s = _ease_centre_msg(age, dur)
    sprite = get_centre_msg_sprite(text)
    if abs(s - 1.0) > 0.005:                
        sw_w = max(1, int(320 * s))
        sw_h = max(1, int(240 * s))
        small = sprite.resize((sw_w, sw_h), Image.BILINEAR)
        sprite2 = Image.new("RGBA", (320, 240), (0, 0, 0, 0)) 
        off_x = (320 - sw_w) // 2
        off_y = (240 - sw_h) // 2
        sprite2.paste(small, (off_x, off_y))
        sprite = sprite2
    if a < 0.999:
        faded = sprite.copy()
        faded.putalpha(sprite.getchannel("A").point(lambda p: int(p * a)))
        sprite = faded
    return Image.alpha_composite(base_rgb.convert("RGBA"), sprite).convert("RGB")

PILL_POP_DUR = 0.30

def _pill_pop_scale(age):
    t = min(1.0, max(0.0, age / PILL_POP_DUR))
    return 0.90 + 0.10 * _ease_out_back(t)        

def render_pill_pop(base_rgb, indicator, scale):
    bbox = indicator.getbbox()
    if bbox is None or abs(scale - 1.0) < 0.005:
        return Image.alpha_composite(base_rgb.convert("RGBA"), indicator).convert("RGB")
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0
    sw_w = max(1, int(320 * scale))
    sw_h = max(1, int(240 * scale))
    resized = indicator.resize((sw_w, sw_h), Image.BILINEAR)
    canvas = Image.new("RGBA", (320, 240), (0, 0, 0, 0)) 
    canvas.paste(resized, (int(round(cx * (1 - scale))), int(round(cy * (1 - scale)))))
    return Image.alpha_composite(base_rgb.convert("RGBA"), canvas).convert("RGB")

_awb_sprite_cache = {}
AWB_POP_DUR = 0.30      

def get_awb_sprite(text, font_size):
    key = (text, font_size)
    if key in _awb_sprite_cache:
        return _awb_sprite_cache[key]
    from PIL import ImageDraw, ImageFilter
    font = load_font(font_size)
    shadow = Image.new("RGBA", (320, 240), (0, 0, 0, 0)) 
    white  = Image.new("RGBA", (320, 240), (0, 0, 0, 0))
    wd = ImageDraw.Draw(white)
    b = wd.textbbox((0, 0), text, font=font)
    ax, ay = 15, 15 - b[1]
    ImageDraw.Draw(shadow).text((ax, ay), text, font=font, fill=(0, 0, 0, 250))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=4))
    wd.text((ax, ay), text, font=font, fill=(255, 255, 255, 255))
    sprite = Image.alpha_composite(shadow, white)
    _awb_sprite_cache[key] = sprite
    return sprite

def _awb_pop_scale(age, full):
    t = min(1.0, max(0.0, age / AWB_POP_DUR))
    e = _ease_out_back(t)
    if full:
        return 0.94 + 0.06 * e     
    return 0.90 + 0.10 * e         

_indicator_cache = {}

def get_filter_indicator(filter_name):
    if filter_name in _indicator_cache:
        return _indicator_cache[filter_name]
    from PIL import ImageDraw, ImageFilter
    S   = 3
    big_w = 320 * S
    big_h = 240 * S  
    pad = 10 * S
    r   = 17 * S
    cx  = big_w - pad - r
    cy  = pad + r
    font = load_font(24 * S)

    shadow = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))
    white  = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    wd = ImageDraw.Draw(white)

    _PILL_LABELS = {"B&W": "B&W", "TRI-X": "TX", "Film Standard": "FS"}

    if filter_name in _PILL_LABELS:
        label  = _PILL_LABELS[filter_name]
        tb     = wd.textbbox((0, 0), label, font=font)
        h_pad  = 10 * S
        pill_h = r * 2
        pill_w = (tb[2] - tb[0]) + h_pad * 2
        x1, y0 = big_w - pad, pad
        x0, y1 = x1 - pill_w, y0 + pill_h
        cr  = pill_h // 2
        pcx = (x0 + x1) // 2
        pcy = (y0 + y1) // 2
        tx  = pcx - (tb[0] + tb[2]) // 2
        ty  = pcy - (tb[1] + tb[3]) // 2
        sd.rounded_rectangle([x0, y0, x1, y1], radius=cr, outline=(0, 0, 0, 250), width=2 * S)
        sd.text((tx, ty), label, font=font, fill=(0, 0, 0, 250))
        wd.rounded_rectangle([x0, y0, x1, y1], radius=cr, outline=(255, 255, 255, 255), width=1 * S)
        wd.text((tx, ty), label, font=font, fill=(255, 255, 255, 255))
    else:
        label = filter_name[0]
        tb = wd.textbbox((0, 0), label, font=font)
        tx = cx - (tb[0] + tb[2]) // 2
        ty = cy - (tb[1] + tb[3]) // 2
        if label in ("D", "P", "L", "N"):
            tx += S
        sd.ellipse([cx-r, cy-r, cx+r, cy+r], outline=(0, 0, 0, 250), width=2 * S)
        sd.text((tx, ty), label, font=font, fill=(0, 0, 0, 250))
        wd.ellipse([cx-r, cy-r, cx+r, cy+r], outline=(255, 255, 255, 255), width=1 * S)
        wd.text((tx, ty), label, font=font, fill=(255, 255, 255, 255))

    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=4 * S))
    layer  = Image.alpha_composite(shadow, white).resize((320, 240), Image.LANCZOS) 
    _indicator_cache[filter_name] = layer
    return layer

_GIF_PILL_PHASES   = 2     
_GIF_PILL_MARCH_HZ = 4     
_gif_indicator_phases = None

def _trace_capsule_perimeter(x0, y0, x1, y1, cr, step=1.5):
    import math
    pts = []
    xa, xb = x0 + cr, x1 - cr
    cyc = y0 + cr
    d = xa                                   
    while d <= xb:
        pts.append((d, y0)); d += step
    a = -math.pi / 2                         
    while a <= math.pi / 2:
        pts.append((xb + cr * math.cos(a), cyc + cr * math.sin(a))); a += step / cr
    d = xb                                   
    while d >= xa:
        pts.append((d, y1)); d -= step
    a = math.pi / 2                          
    while a <= 3 * math.pi / 2:
        pts.append((xa + cr * math.cos(a), cyc + cr * math.sin(a))); a += step / cr
    return pts

def _draw_dashed_path(draw, pts, phase, dash, gap, width, fill):
    import math
    period = dash + gap
    acc = 0.0
    for p, q in zip(pts, pts[1:] + pts[:1]):
        if ((acc + phase) % period) < dash:
            draw.line([p, q], fill=fill, width=width)
        acc += math.hypot(q[0] - p[0], q[1] - p[1])

def _build_gif_indicator_phases():
    from PIL import ImageDraw, ImageFilter
    S   = 3
    big_w = 320 * S
    big_h = 240 * S
    font = load_font(24 * S)
    label = "GIF"
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tb = tmp.textbbox((0, 0), label, font=font)
    pad    = 10 * S
    pill_h = 34 * S
    pill_w = (tb[2] - tb[0]) + 20 * S
    x1 = big_w - pad
    x0 = x1 - pill_w
    y0 = pad + pill_h + 8 * S
    y1 = y0 + pill_h
    cr = pill_h // 2
    tx = x0 + (pill_w - (tb[0] + tb[2])) // 2
    ty = y0 + (pill_h - (tb[1] + tb[3])) // 2
    pts = _trace_capsule_perimeter(x0, y0, x1, y1, cr, step=2.0)
    dash, gap = 6 * S, 5 * S
    period = dash + gap
    layers = []
    for k in range(_GIF_PILL_PHASES):
        phase = period * k / _GIF_PILL_PHASES   
        shadow = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))
        white  = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        wd = ImageDraw.Draw(white)
        _draw_dashed_path(sd, pts, phase, dash, gap, 3 * S, (0, 0, 0, 250))
        _draw_dashed_path(wd, pts, phase, dash, gap, 2 * S, (255, 255, 255, 255))
        sd.text((tx, ty), label, font=font, fill=(0, 0, 0, 250))
        wd.text((tx, ty), label, font=font, fill=(255, 255, 255, 255))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=4 * S))
        layer = Image.alpha_composite(shadow, white).resize((320, 240), Image.LANCZOS)
        layers.append(layer)
    return layers

def get_gif_mode_indicator(phase_index=0):
    global _gif_indicator_phases
    if _gif_indicator_phases is None:
        _gif_indicator_phases = _build_gif_indicator_phases()
    return _gif_indicator_phases[phase_index % _GIF_PILL_PHASES]

_font_cache = {}  
_font_warned = False
def load_font(size):
    global _font_warned
    if size in _font_cache:
        return _font_cache[size]
    from PIL import ImageFont
    try:
        _font_cache[size] = ImageFont.truetype(FONT_PATH, size)
    except (OSError, IOError) as e:
        if not _font_warned:
            log(f"TTF load failed ({FONT_PATH}): {e!r} — using bitmap default")
            _font_warned = True
        _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]

# ── Filters ────────────────────────────────────────────────────────────────
FILTERS = ["Film Standard", "Punch", "B&W", "Deep", "Sand", "Eterna", "TRI-X", "Cutout", "No Filter"]

def _make_lut(points):
    x = [p[0] for p in points]
    y = [p[1] for p in points]
    return np.interp(np.arange(256), x, y).clip(0, 255).astype(np.uint8)

_BASE_CURVES = {
    "B&W":        _make_lut([(0,0),(64,16),(128,160),(192,242),(255,255)]),   
    "Punch":      _make_lut([(0,0),(64,52),(128,148),(192,212),(255,242)]),   
    "Sand":       _make_lut([(0,0),(64,50),(128,132),(192,205),(255,255)]),   
    "Deep":       _make_lut([(0,30),(64,70),(128,152),(192,222),(255,255)]),
    "Eterna":        _make_lut([(0,30),(64,78),(128,128),(192,172),(255,215)]),  
    "Film Standard": _make_lut([(0,18),(64,55),(128,140),(192,210),(255,252)]),  
}

_CHANNEL_LUTS = {}
_v = np.arange(256, dtype=np.float32)

_CHANNEL_LUTS["B&W"] = (
    _BASE_CURVES["B&W"],
    _BASE_CURVES["B&W"],
    _BASE_CURVES["B&W"],
)

_pc = _BASE_CURVES["Punch"].astype(np.float32)
_punch_shadow_blue = np.maximum(0.0, 65.0 * (1.0 - _v / 105.0))
_CHANNEL_LUTS["Punch"] = (
    np.clip(_pc * 1.05, 0, 255).astype(np.uint8),
    np.clip(_pc * 1.02, 0, 255).astype(np.uint8),
    np.clip(_pc + _punch_shadow_blue, 0, 255).astype(np.uint8),
)

_sc = _BASE_CURVES["Sand"].astype(np.float32)
_CHANNEL_LUTS["Sand"] = (
    np.clip(_sc * 1.08, 0, 255).astype(np.uint8),   
    np.clip(_sc * 0.92, 0, 255).astype(np.uint8),   
    np.clip(_sc * 0.55, 0, 255).astype(np.uint8),   
)

_dc = _BASE_CURVES["Deep"].astype(np.float32)
_CHANNEL_LUTS["Deep"] = (
    np.clip(_dc * 0.55, 0, 255).astype(np.uint8),   
    np.clip(_dc * 0.70, 0, 255).astype(np.uint8),   
    np.clip(_dc * 1.35, 0, 255).astype(np.uint8),   
)

_et = _BASE_CURVES["Eterna"].astype(np.float32)
_CHANNEL_LUTS["Eterna"] = (
    np.clip(_et * 0.96, 0, 255).astype(np.uint8),   
    np.clip(_et * 1.00, 0, 255).astype(np.uint8),   
    np.clip(_et * 1.05, 0, 255).astype(np.uint8),   
)

_fs = _BASE_CURVES["Film Standard"].astype(np.float32)
_CHANNEL_LUTS["Film Standard"] = (
    np.clip(_fs * 0.95, 0, 255).astype(np.uint8),   
    np.clip(_fs * 1.02, 0, 255).astype(np.uint8),   
    np.clip(_fs * 1.08, 0, 255).astype(np.uint8),   
)

_co = np.zeros(256, dtype=np.uint8)
_co[65:130] = 128    
_co[130:]   = 255    
_CHANNEL_LUTS["Cutout"] = (_co, _co, _co)


_TRITON_SHADOW    = np.array([0,   0,   0],   dtype=np.float32)
_TRITON_MID       = np.array([242, 183,  8],  dtype=np.float32)  
_TRITON_HIGHLIGHT = np.array([35,  155, 60],  dtype=np.float32)  
_trix_lut = np.zeros((256, 3), dtype=np.float32)
_TRIX_BLACK = 0.18   
_TRIX_SPLIT = 0.38   
for _i in range(256):
    _t = _i / 255.0
    if _t <= _TRIX_BLACK:
        _trix_lut[_i] = _TRITON_SHADOW
    elif _t <= _TRIX_SPLIT:
        _t2 = (_t - _TRIX_BLACK) / (_TRIX_SPLIT - _TRIX_BLACK)
        _trix_lut[_i] = _TRITON_SHADOW + (_TRITON_MID - _TRITON_SHADOW) * _t2
    else:
        _t2 = (_t - _TRIX_SPLIT) / (1.0 - _TRIX_SPLIT)
        _trix_lut[_i] = _TRITON_MID + (_TRITON_HIGHLIGHT - _TRITON_MID) * _t2
_TRIX_LUT = np.clip(_trix_lut, 0, 255).astype(np.uint8)   

_GRAIN_TABLE_SIZE = 1024
_grain_tables: dict = {}
_grain_rng = np.random.default_rng(0)  

def _get_grain_table(intensity: int) -> np.ndarray:
    if intensity not in _grain_tables:
        _grain_tables[intensity] = _grain_rng.integers(
            -intensity, intensity + 1,
            (_GRAIN_TABLE_SIZE, _GRAIN_TABLE_SIZE),
            dtype=np.int16,
        )
    return _grain_tables[intensity]

def _apply_grain(arr: np.ndarray, intensity: int) -> np.ndarray:
    h, w = arr.shape[:2]
    table = _get_grain_table(intensity)
    dy = np.random.randint(0, _GRAIN_TABLE_SIZE)
    dx = np.random.randint(0, _GRAIN_TABLE_SIZE)
    rows = (np.arange(dy, dy + h) % _GRAIN_TABLE_SIZE)[:, np.newaxis]  
    cols = (np.arange(dx, dx + w) % _GRAIN_TABLE_SIZE)[np.newaxis, :]  
    grain = table[rows, cols]                                           
    arr16 = arr.astype(np.int16)
    arr16 += grain[:, :, np.newaxis]   
    return np.clip(arr16, 0, 255).astype(np.uint8)

_GRAIN = {"B&W": 27, "Punch": 22, "Sand": 22, "Deep": 22, "Cutout": 22, "TRI-X": 15, "Eterna": 15, "Film Standard": 18}

_FILM_ISP = {
    "No Filter": {"Saturation": 1.0, "Contrast": 1.0, "Brightness": 0.0},
    "B&W":    {"Saturation": 0.0, "Contrast": 1.0, "Brightness": 0.0},
    "Punch":  {"Saturation": 1.3, "Contrast": 1.0, "Brightness": -0.05},
    "Sand":   {"Saturation": 0.45,"Contrast": 1.0, "Brightness": 0.0},
    "Deep":   {"Saturation": 0.6, "Contrast": 1.0, "Brightness": 0.0},
    "Cutout": {"Saturation": 0.0, "Contrast": 1.0, "Brightness": 0.0},
    "TRI-X":       {"Saturation": 1.0, "Contrast": 1.0, "Brightness": 0.0},
    "Eterna":        {"Saturation": 0.75, "Contrast": 1.0, "Brightness": 0.0},
    "Film Standard": {"Saturation": 0.85, "Contrast": 1.0, "Brightness": 0.0},
}

def _apply_filter_by_name(image, name, apply_grain=True):
    if name == "No Filter":
        return image
    arr = np.array(image, dtype=np.uint8)
    if name == "TRI-X":
        luma = (arr[:, :, 0].astype(np.uint32) * 299 +
                arr[:, :, 1].astype(np.uint32) * 587 +
                arr[:, :, 2].astype(np.uint32) * 114 + 500) // 1000
        arr = _TRIX_LUT[luma.clip(0, 255).astype(np.uint8)]  
    elif name == "Cutout":
        p = _co[np.array(image.convert('L'), dtype=np.uint8)]
        arr = np.stack([p, p, p], axis=2)
    else:
        r_lut, g_lut, b_lut = _CHANNEL_LUTS[name]
        arr[:, :, 0] = r_lut[arr[:, :, 0]]
        arr[:, :, 1] = g_lut[arr[:, :, 1]]
        arr[:, :, 2] = b_lut[arr[:, :, 2]]
    grain = _GRAIN.get(name, 0) if apply_grain else 0
    if grain:
        arr = _apply_grain(arr, grain)
    return Image.fromarray(arr)

def apply_filter(image):
    return _apply_filter_by_name(image, FILTERS[filter_index], apply_grain=False)

def send_command(cmd):
    GPIO.output(DC_PIN, GPIO.LOW)
    spi.xfer([cmd])
def send_data(data):
    GPIO.output(DC_PIN, GPIO.HIGH)
    chunk_size = 131072
    for i in range(0, len(data), chunk_size):
        chunk = data[i:i + chunk_size]
        try:
            spi.writebytes2(chunk)
        except AttributeError:
            spi.writebytes(chunk)

PANEL_WIDTH = 320
PANEL_HEIGHT = 240
CANVAS_WIDTH = 320
CANVAS_HEIGHT = 240

def _set_window(x0, y0, x1, y1):
    send_command(0x2A)
    GPIO.output(DC_PIN, GPIO.HIGH)
    spi.writebytes([(x0 >> 8) & 0xFF, x0 & 0xFF, (x1 >> 8) & 0xFF, x1 & 0xFF])
    send_command(0x2B)
    GPIO.output(DC_PIN, GPIO.HIGH)
    spi.writebytes([(y0 >> 8) & 0xFF, y0 & 0xFF, (y1 >> 8) & 0xFF, y1 & 0xFF])
    send_command(0x2C)

def init_display():
    print("Initializing display (ILI9341 320x240)...")
    GPIO.output(RST_PIN, GPIO.HIGH)
    time.sleep(0.05)
    GPIO.output(RST_PIN, GPIO.LOW)
    time.sleep(0.05)
    GPIO.output(RST_PIN, GPIO.HIGH)
    time.sleep(0.12)
    init_commands = [
        (0xEF, [0x03, 0x80, 0x02]),
        (0xCF, [0x00, 0xC1, 0x30]),
        (0xED, [0x64, 0x03, 0x12, 0x81]),
        (0xE8, [0x85, 0x00, 0x78]),
        (0xCB, [0x39, 0x2C, 0x00, 0x34, 0x02]),
        (0xF7, [0x20]),
        (0xEA, [0x00, 0x00]),
        (0xC0, [0x23]),                        
        (0xC1, [0x10]),                        
        (0xC5, [0x3E, 0x28]),                  
        (0xC7, [0x86]),                        
        (0x36, [0x28]), # <--- LANDSCAPE MADCTL                        
        (0x3A, [0x55]),                        
        (0xB1, [0x00, 0x18]),                  
        (0xB6, [0x08, 0x82, 0x27]),            
        (0xF2, [0x00]),                        
        (0x26, [0x01]),                        
        (0xE0, [0x0F, 0x31, 0x2B, 0x0C, 0x0E, 0x08, 0x4E, 0xF1,
                0x37, 0x07, 0x10, 0x03, 0x0E, 0x09, 0x00]),   
        (0xE1, [0x00, 0x0E, 0x14, 0x03, 0x11, 0x07, 0x31, 0xC1,
                0x48, 0x08, 0x0F, 0x0C, 0x31, 0x36, 0x0F]),   
        (0x11, []),                            
    ]
    for cmd, data in init_commands:
        send_command(cmd)
        if data:
            GPIO.output(DC_PIN, GPIO.HIGH)
            spi.xfer(data)
        if cmd == 0x11:
            time.sleep(0.12)
    send_command(0x29)                         
def set_backlight(state):
    _pi.set_PWM_dutycycle(BL_PIN, 255 if state else 0)
def set_backlight_brightness(pct):
    _pi.set_PWM_dutycycle(BL_PIN, int(pct * 2.55))
def clear_display():
    with display_lock:
        _set_window(0, 0, PANEL_WIDTH - 1, PANEL_HEIGHT - 1)
        send_data(bytearray(PANEL_WIDTH * PANEL_HEIGHT * 2))

_CONTRAST_LUT = np.clip(
    (np.arange(256, dtype=np.float32) - 128) * 1.15 + 123,
    0, 255
).astype(np.uint8)

_c16 = _CONTRAST_LUT.astype(np.uint16)
_R565_LUT = ((_c16 & 0xF8) << 8).astype(np.uint16)
_G565_LUT = ((_c16 & 0xFC) << 3).astype(np.uint16)
_B565_LUT = ((_c16 & 0xF8) >> 3).astype(np.uint16)

def convert_to_rgb565(image):
    a = np.asarray(image, dtype=np.uint8)
    rgb565 = _R565_LUT[a[:, :, 0]] | _G565_LUT[a[:, :, 1]] | _B565_LUT[a[:, :, 2]]
    return rgb565.astype('>u2').tobytes()
def display_image(image):
    with display_lock:
        _set_window(0, 0, PANEL_WIDTH - 1, PANEL_HEIGHT - 1)
        send_data(convert_to_rgb565(image))

def show_splash():
    splash_path = "/home/dkumkum/splash.raw"
    if not os.path.exists(splash_path):
        return
    with open(splash_path, "rb") as f:
        data = f.read()
    with display_lock:
        x_off = 40
        _set_window(x_off, 0, x_off + 239, 239)
        send_data(data)

def show_transfer_mode_screen():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (320, 240), (0, 0, 0)) 
    draw = ImageDraw.Draw(img)
    font_title = load_font(17)
    font_label = load_font(16)
    font_value = load_font(20)
    font_hint  = load_font(18)

    try:
        result = subprocess.run(["iw", "dev", "uap0", "station", "dump"],
                                capture_output=True, text=True, timeout=1)
        device_count = result.stdout.count("Station ")
        device_connected = device_count > 0
    except Exception:
        device_count = 0
        device_connected = False

    dot_visible = device_connected or (int(time.time() * 2) % 2 == 0)
    dot_color   = (60, 200, 80) if device_connected else (90, 90, 90)
    dot_r = 6

    title = "Transfer Mode"
    tb = draw.textbbox((0, 0), title, font=font_title)
    title_y = 15 
    draw.text((20, title_y), title, font=font_title, fill=(160, 160, 160))
    dot_cy = title_y + (tb[1] + tb[3]) // 2
    dot_cx = 294
    if dot_visible:
        draw.ellipse([dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r], fill=dot_color)

    if device_connected:
        count_str = str(device_count)
        cb = draw.textbbox((0, 0), count_str, font=font_title)
        count_w = cb[2] - cb[0]
        count_x = dot_cx - dot_r - count_w - 8
        count_y = dot_cy - (cb[3] - cb[1]) // 2 - cb[1]
        draw.text((count_x, count_y), count_str, font=font_title, fill=dot_color)

    draw.line([(8, 45), (312, 45)], fill=(40, 40, 40), width=1)
    draw.text((20, 55),  "WiFi",         font=font_label, fill=(100, 100, 100))
    draw.text((20, 73),  "RETROCAM", font=font_value, fill=(255, 255, 255))
    draw.text((20, 103),  "Password",     font=font_label, fill=(100, 100, 100))
    draw.text((20, 121), "cryptgod",     font=font_value, fill=(255, 255, 255))
    draw.text((20, 151), "Browser",      font=font_label, fill=(100, 100, 100))
    draw.text((20, 169), "192.168.4.1",  font=font_value, fill=(255, 255, 255))
    draw.line([(8, 195), (312, 195)], fill=(40, 40, 40), width=1)

    hint = "Hold center to exit"
    hb = draw.textbbox((0, 0), hint, font=font_hint)
    hint_h = hb[3] - hb[1]
    hint_y = 205
    draw.text(((320 - (hb[2] - hb[0])) // 2, hint_y), hint, font=font_hint, fill=(60, 60, 60))

    display_image(img)

GALLERY_DIR = "/home/dkumkum/photos"

_capture_counter = None
_capture_counter_lock = threading.Lock()

def _capture_number_of(filename):
    if not filename.startswith("RetroCam_"):
        return None
    stem = filename[len("RetroCam_"):]
    dot = stem.rfind(".")
    if dot == -1:
        return None
    num = stem[:dot]
    ext = stem[dot:].lower()
    if ext in (".jpg", ".gif") and num.isdigit():
        return int(num)
    return None

def get_next_capture_number():
    global _capture_counter
    with _capture_counter_lock:
        if _capture_counter is None:
            try:
                numbers = [
                    n for f in os.listdir(GALLERY_DIR)
                    if (n := _capture_number_of(f)) is not None
                ] if os.path.exists(GALLERY_DIR) else []
                _capture_counter = max(numbers) + 1 if numbers else 1
            except:
                _capture_counter = 1
        num = _capture_counter
        _capture_counter += 1
        return num

def get_gallery_images():
    try:
        if not os.path.exists(GALLERY_DIR):
            print("Gallery dir not found")
            return []
        files = [
            os.path.join(GALLERY_DIR, f)
            for f in os.listdir(GALLERY_DIR)
            if _capture_number_of(f) is not None
        ]
        files.sort(key=lambda f: _capture_number_of(os.path.basename(f)))
        print(f"Found {len(files)} images")
        return files
    except Exception as e:
        print(f"Gallery scan error: {e}")
        return []

def display_gallery_image(filepath, index, total, confirm_delete=False):
    try:
        from PIL import ImageDraw, ImageFilter
        img = Image.open(filepath)
        img.draft("RGB", (320, 240))
        img = img.convert("RGB")
        img = img.resize((320, 240), Image.BILINEAR) 

        font = load_font(25)
        text = f"{index}/{total}"
        draw = ImageDraw.Draw(img)
        bbox_t = draw.textbbox((0, 0), text, font=font)
        text_h = bbox_t[3] - bbox_t[1]
        x, y = 15, 240 - 15 - text_h 
        shadow = Image.new("RGBA", (320, 240), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).text((x, y), text, font=font, fill=(0, 0, 0, 250))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=4))
        img = img.convert("RGBA")
        img = Image.alpha_composite(img, shadow)
        img = img.convert("RGB")
        draw = ImageDraw.Draw(img)
        draw.text((x, y), text, font=font, fill=(255, 255, 255))

        if confirm_delete:
            overlay = Image.new("RGBA", (320, 240), (0, 0, 0, 160))
            img = img.convert("RGBA")
            img = Image.alpha_composite(img, overlay)
            img = img.convert("RGB")
            draw = ImageDraw.Draw(img)
            font_dialog = load_font(25)
            font_yes = font_dialog
            t1 = "Delete?"
            b1 = draw.textbbox((0, 0), t1, font=font_dialog)
            draw.text(((320 - (b1[2] - b1[0])) // 2, 80), t1, font=font_dialog, fill=(255, 255, 255))
            t2 = "YES: "
            b2 = draw.textbbox((0, 0), t2, font=font_yes)
            text_w = b2[2] - b2[0]
            text_h = b2[3] - b2[1]
            arrow_w = 14
            bx = (320 - text_w - arrow_w) // 2
            by = 118
            draw.text((bx, by), t2, font=font_yes, fill=(255, 255, 255))
            ax = bx + text_w
            mid_y = by + (b2[1] + b2[3]) // 2
            draw.polygon([(ax + 7, mid_y - 8), (ax, mid_y + 6), (ax + 14, mid_y + 6)], fill=(255, 255, 255))
            t3 = "NO: Any Button"
            b3 = draw.textbbox((0, 0), t3, font=font_yes)
            draw.text(((320 - (b3[2] - b3[0])) // 2, by + text_h + 10), t3, font=font_yes, fill=(180, 180, 180))

        display_image(img)
    except Exception as e:
        print(f"Gallery load error: {e}")

def _build_gif_static_overlay(index, total):
    from PIL import ImageDraw, ImageFilter
    font_lbl = load_font(25)
    shadow = Image.new("RGBA", (320, 240), (0, 0, 0, 0)) 
    white  = Image.new("RGBA", (320, 240), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    wd = ImageDraw.Draw(white)

    bl = wd.textbbox((0, 0), "GIF", font=font_lbl)
    lx, ly = 15, 15 - bl[1]
    sd.text((lx, ly), "GIF", font=font_lbl, fill=(0, 0, 0, 250))
    wd.text((lx, ly), "GIF", font=font_lbl, fill=(255, 255, 255, 255))

    pos = f"{index}/{total}"
    bp = wd.textbbox((0, 0), pos, font=font_lbl)
    px, py = 15, 240 - 15 - (bp[3] - bp[1]) 
    sd.text((px, py), pos, font=font_lbl, fill=(0, 0, 0, 250))
    wd.text((px, py), pos, font=font_lbl, fill=(255, 255, 255, 255))

    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=4))
    return Image.alpha_composite(shadow, white)

def _overlay_gif_frame(rgb_frame, static, font_fc, i, n):
    from PIL import ImageDraw
    f = Image.alpha_composite(rgb_frame.convert("RGBA"), static).convert("RGB")
    fc = f"{i+1}/{n}"
    d = ImageDraw.Draw(f)
    b = d.textbbox((0, 0), fc, font=font_fc)
    x, y = 320 - 15 - b[2], 15 - b[1]
    d.text((x + 1, y + 1), fc, font=font_fc, fill=(60, 60, 60))
    d.text((x, y), fc, font=font_fc, fill=(255, 255, 255))
    return f

_gif_load_token = 0

def start_gif_gallery_load(filepath, index, total):
    global _gif_load_token
    _gif_load_token += 1
    token = _gif_load_token
    frames = []
    try:
        gif = Image.open(filepath)
        n = getattr(gif, "n_frames", 1)
        static = _build_gif_static_overlay(index, total)
        font_fc = load_font(22)
        gif.seek(0)
        frames.append(_overlay_gif_frame(
            gif.convert("RGB").resize((320, 240), Image.BILINEAR), static, font_fc, 0, n))

        def decode_rest():
            try:
                g = Image.open(filepath)
                for i in range(1, n):
                    if token != _gif_load_token:
                        return   
                    g.seek(i)
                    frames.append(_overlay_gif_frame(
                        g.convert("RGB").resize((320, 240), Image.BILINEAR), static, font_fc, i, n))
            except Exception as e:
                print(f"GIF bg decode error: {e}")

        if n > 1:
            threading.Thread(target=decode_rest, daemon=True).start()
    except Exception as e:
        print(f"GIF gallery load error: {e}")
    return frames

def _save_image_async(captured_image, filepath, filename, film_name="No Filter"):
    global saving_active
    try:
        start = time.time()
        captured_image = _apply_filter_by_name(captured_image, film_name)
        captured_image.save(filepath, "JPEG", quality=98, optimize=True)
        with open(filepath, "rb") as f:
            os.fsync(f.fileno())

        if not os.path.exists(filepath):
            print("✗ File not created")
            return

        file_size = os.path.getsize(filepath)
        if file_size < 100000:
            print(f"✗ File too small ({file_size} bytes) - DELETING")
            try:
                os.remove(filepath)
            except:
                pass
            return

        try:
            test_img = Image.open(filepath)
            test_img.verify()
            test_img.close()
        except Exception as e:
            print(f"✗ Corrupted: {e} - DELETING")
            try:
                os.remove(filepath)
            except:
                pass
            return

        print(f"✓ Saved {filename} ({file_size/1024/1024:.2f} MB) in {time.time()-start:.2f}s")
    except Exception as e:
        print(f"✗ Save error: {e}")
    finally:
        with _save_active_lock:
            saving_active -= 1

def capture_full_res(picam2):
    global capturing, camera_started, config_cache, saving_active
    captured_image = None
    filepath = None
    filename = None

    try:
        with camera_lock:
            capturing = True
            print("\n=== CAPTURE ===")

            os.makedirs(GALLERY_DIR, exist_ok=True)
            number = get_next_capture_number()
            filename = f"RetroCam_{number}.jpg"
            filepath = os.path.join(GALLERY_DIR, filename)

            if camera_started:
                picam2.stop()
                camera_started = False
                time.sleep(0.02)

            picam2.configure(config_cache.get_capture_config())
            picam2.start()
            picam2.set_controls(get_camera_controls())
            time.sleep(0.15)

            for attempt in range(2):
                try:
                    captured_image = picam2.capture_image()
                    if captured_image and captured_image.size[0] > 0:
                        print(f"✓ Captured: {captured_image.size}")
                        break
                    captured_image = None
                    time.sleep(0.08)
                except Exception as e:
                    print(f"⚠ Attempt {attempt + 1}: {e}")
                    captured_image = None
                    time.sleep(0.08)

            picam2.stop()
            time.sleep(0.02)

            if captured_image is None:
                print("✗ Capture failed")
                return None

            captured_image = captured_image.transpose(Image.FLIP_LEFT_RIGHT)

        capturing = False
        print("✓ Preview resuming...")

        with _save_active_lock:
            saving_active += 1
        threading.Thread(
            target=_save_image_async,
            args=(captured_image, filepath, filename, FILTERS[filter_index]),
            daemon=True
        ).start()

        return filepath

    except Exception as e:
        print(f"✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        capturing = False
        return None

GIF_FRAMES   = 10     
GIF_FPS      = 2      
GIF_DURATION = 5      
GIF_FRAME_MS = 1000 // GIF_FPS   

def _save_gif_async(frames, filepath, filename):
    global saving_active
    try:
        start = time.time()
        palette_src = frames[0].convert("P", palette=Image.ADAPTIVE, colors=256)
        pal_frames = [
            f.quantize(palette=palette_src, dither=Image.NONE) for f in frames
        ]
        pal_frames[0].save(
            filepath, save_all=True, append_images=pal_frames[1:],
            duration=GIF_FRAME_MS, loop=0, optimize=False, disposal=1,
        )
        with open(filepath, "rb") as f:
            os.fsync(f.fileno())

        if not os.path.exists(filepath) or os.path.getsize(filepath) < 1000:
            print("✗ GIF too small / not created - DELETING")
            try:
                os.remove(filepath)
            except:
                pass
            return

        file_size = os.path.getsize(filepath)
        print(f"✓ Saved {filename} ({file_size/1024:.0f} KB) in {time.time()-start:.2f}s")
    except Exception as e:
        print(f"✗ GIF save error: {e}")
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except:
            pass
    finally:
        with _save_active_lock:
            saving_active -= 1

_rec_num_cache = {}

def _get_rec_number_layer(num):
    if num in _rec_num_cache:
        return _rec_num_cache[num]
    from PIL import ImageDraw, ImageFilter
    font_num = load_font(49)
    shadow = Image.new("RGBA", (320, 240), (0, 0, 0, 0)) 
    white  = Image.new("RGBA", (320, 240), (0, 0, 0, 0))
    wd = ImageDraw.Draw(white)
    b = wd.textbbox((0, 0), num, font=font_num)
    nx = (320 - (b[2] - b[0])) // 2 - b[0]
    ny = (240 - (b[3] - b[1])) // 2 - b[1] 
    ImageDraw.Draw(shadow).text((nx, ny), num, font=font_num, fill=(0, 0, 0, 250))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=5))
    wd.text((nx, ny), num, font=font_num, fill=(255, 255, 255, 255))
    layer = Image.alpha_composite(shadow, white)
    _rec_num_cache[num] = layer
    return layer

def _draw_rec_overlay(image, count, age=1.0):
    from PIL import ImageDraw
    img = image.convert("RGBA")
    ImageDraw.Draw(img).rectangle([0, 0, 319, 239], outline=(235, 45, 45), width=3) 
    layer = _get_rec_number_layer(str(count))
    if age < 0.13:
        t = age / 0.13
        s = 0.95 + 0.05 * (1 - (1 - t) ** 3)
        sw_w = max(1, int(320 * s))
        sw_h = max(1, int(240 * s))
        small = layer.resize((sw_w, sw_h), Image.BILINEAR)
        layer = Image.new("RGBA", (320, 240), (0, 0, 0, 0))
        off_x = (320 - sw_w) // 2
        off_y = (240 - sw_h) // 2
        layer.paste(small, (off_x, off_y))

    img = Image.alpha_composite(img, layer)
    return img.convert("RGB")

def record_gif(picam2):
    global capturing, camera_started, config_cache, saving_active, gif_recording, gif_cancel_requested

    frames = []
    film_name = FILTERS[filter_index]
    filepath = None
    filename = None
    cancelled = False

    try:
        with camera_lock:
            gif_recording = True
            gif_cancel_requested = False
            print("\n=== GIF RECORD ===")

            os.makedirs(GALLERY_DIR, exist_ok=True)
            number = get_next_capture_number()
            filename = f"RetroCam_{number}.gif"
            filepath = os.path.join(GALLERY_DIR, filename)

            if camera_started:
                picam2.stop()
                camera_started = False
                time.sleep(0.05)
            picam2.configure(config_cache.get_gif_config())
            picam2.start()
            picam2.set_controls(get_camera_controls())
            time.sleep(0.15)

            rec_label = None
            rec_label_start = 0.0

            preroll_end = time.time() + 0.3
            while time.time() < preroll_end:
                if gif_cancel_requested:
                    cancelled = True
                    break
                try:
                    req = picam2.capture_request()
                    frame = req.make_image("main")
                    req.release()
                    disp = frame.transpose(Image.FLIP_LEFT_RIGHT).resize((320, 240), Image.BILINEAR)
                    disp = _apply_filter_by_name(disp, film_name, apply_grain=False)
                    lbl = f"1/{GIF_FRAMES}"
                    if lbl != rec_label:
                        rec_label, rec_label_start = lbl, time.time()
                    display_image(_draw_rec_overlay(disp, lbl, time.time() - rec_label_start))
                except Exception:
                    pass
                time.sleep(0.01)

            interval = 1.0 / GIF_FPS
            rec_start = time.time()
            for i in range(GIF_FRAMES):
                if cancelled:
                    break
                target = rec_start + i * interval
                while True:
                    if gif_cancel_requested:
                        cancelled = True
                        break
                    now = time.time()
                    frame_label = f"{i+1}/{GIF_FRAMES}"   
                    try:
                        req = picam2.capture_request()
                        frame = req.make_image("main")
                        req.release()
                    except Exception:
                        frame = None
                    if frame is not None:
                        frame = frame.transpose(Image.FLIP_LEFT_RIGHT)
                        disp = frame.resize((320, 240), Image.BILINEAR)
                        disp = _apply_filter_by_name(disp, film_name, apply_grain=False)
                        if frame_label != rec_label:
                            rec_label, rec_label_start = frame_label, now
                        display_image(_draw_rec_overlay(disp, frame_label, now - rec_label_start))
                    if now >= target:
                        break
                    time.sleep(0.01)

                if not cancelled and frame is not None:
                    gif_frame = _apply_filter_by_name(frame, film_name, apply_grain=False)
                    frames.append(gif_frame)

            picam2.stop()
            time.sleep(0.05)

        if cancelled:
            frames = []
            print("✗ GIF recording cancelled — discarded")
        else:
            print(f"✓ Captured {len(frames)} frames, encoding…")
            if frames:
                with _save_active_lock:
                    saving_active += 1
                threading.Thread(
                    target=_save_gif_async,
                    args=(frames, filepath, filename),
                    daemon=True
                ).start()

    except Exception as e:
        print(f"✗ GIF ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        gif_recording = False
        gif_cancel_requested = False
        camera_started = False

def draw_pill_outline(draw_obj, x, y, bbox):
    pad_x, pad_y = 6, 4
    x0 = x + bbox[0] - pad_x
    y0 = y + bbox[1] - pad_y
    x1 = x + bbox[2] + pad_x
    y1 = y + bbox[3] + pad_y
    draw_obj.rounded_rectangle([x0, y0, x1, y1], radius=6, outline=(255, 255, 255, 180), width=2)

def button_handler():
    global preview_active, capture_requested, exit_requested
    global gallery_active, gallery_index, gallery_images, gallery_needs_update, gallery_confirm_delete, gallery_empty_message_time
    global awb_mode_index, awb_mode_changed, awb_changed_time, gallery_empty_message_time, gallery_confirm_delete, no_space_message_time, splash_active
    global filter_index, filter_label_time, isp_changed
    global transfer_mode, transfer_screen_shown, _transfer_last_activity, _transfer_dimmed
    global _idle_last_activity, _idle_dimmed
    global gif_mode, gif_record_requested, gif_mode_label_time, gif_cancel_requested
    global active_setting_index, ev_index, iso_index, shutter_index

    last_capture = 0
    last_joy_press = 0
    last_joy_up = 0
    last_joy_down = 0
    debounce = 0.3

    cap_was_down = False
    cap_down_time = 0
    cap_long_fired = False
    CAP_LONG_THRESHOLD = 0.6
    
    prev_was_down = False
    prev_down_time = 0
    prev_long_fired = False
    PREV_LONG_THRESHOLD = 0.6

    left_held_since = 0
    right_held_since = 0
    last_scroll_time = 0
    HOLD_THRESHOLD = 0.5
    FAST_INTERVAL = 0.15

    joy_press_times = []
    TRIPLE_PRESS_WINDOW = 0.8
    joy_press_was_down = False
    joy_press_down_time = 0
    joy_long_press_fired = False

    print("✓ Buttons ready")

    while not exit_requested:
        try:
            now = time.time()

            if splash_active:
                any_pressed = (
                    not GPIO.input(BUTTON_CAPTURE) or
                    not GPIO.input(BUTTON_PREVIEW) or
                    not GPIO.input(BUTTON_UP) or
                    not GPIO.input(BUTTON_DOWN) or
                    not GPIO.input(BUTTON_LEFT) or
                    not GPIO.input(BUTTON_RIGHT) or
                    not GPIO.input(BUTTON_PRESS)
                )
                if any_pressed:
                    splash_active = False
                    _idle_last_activity = time.time()
                    joy_press_times.clear()
                    print("Splash closed")
                time.sleep(0.05)
                continue

            if not transfer_mode:
                any_input = (
                    not GPIO.input(BUTTON_CAPTURE) or
                    not GPIO.input(BUTTON_PREVIEW) or
                    not GPIO.input(BUTTON_UP) or
                    not GPIO.input(BUTTON_DOWN) or
                    not GPIO.input(BUTTON_LEFT) or
                    not GPIO.input(BUTTON_RIGHT) or
                    not GPIO.input(BUTTON_PRESS)
                )
                if any_input:
                    _idle_last_activity = time.time()
                    if _idle_dimmed:
                        _idle_dimmed = False
                        set_backlight(True)
                        time.sleep(0.3)
                        continue

            if not GPIO.input(BUTTON_UP):
                if now - last_joy_up > debounce:
                    last_joy_up = now
                    if gallery_active and gallery_images:
                        if gallery_confirm_delete:
                            filepath = gallery_images[gallery_index]
                            try:
                                os.remove(filepath)
                                print(f"✓ Deleted: {os.path.basename(filepath)}")
                                fname = os.path.basename(filepath)
                                thumb_dir = os.path.join(GALLERY_DIR, ".thumbs")
                                for f in os.listdir(thumb_dir) if os.path.exists(thumb_dir) else []:
                                    if f.startswith(fname + "_"):
                                        try: os.remove(os.path.join(thumb_dir, f))
                                        except: pass
                            except Exception as e:
                                print(f"✗ Delete error: {e}")
                            gallery_images.pop(gallery_index)
                            gallery_confirm_delete = False
                            if not gallery_images:
                                gallery_active = False
                                preview_active = True
                                print("Gallery empty, closing")
                            else:
                                gallery_index = min(gallery_index, len(gallery_images) - 1)
                                gallery_needs_update = True
                        else:
                            gallery_confirm_delete = True
                            gallery_needs_update = True
                    elif preview_active and not capturing:
                        filter_index = (filter_index - 1) % len(FILTERS)
                        filter_label_time = now
                        isp_changed = True
                        print(f"Filter: {FILTERS[filter_index]}")

            if not GPIO.input(BUTTON_DOWN):
                if now - last_joy_down > debounce:
                    last_joy_down = now
                    if gallery_active and gallery_confirm_delete:
                        gallery_confirm_delete = False
                        gallery_needs_update = True
                    elif preview_active and not capturing:
                        filter_index = (filter_index + 1) % len(FILTERS)
                        filter_label_time = now
                        isp_changed = True
                        print(f"Filter: {FILTERS[filter_index]}")

            joy_is_down = not GPIO.input(BUTTON_PRESS)
            if joy_is_down and not joy_press_was_down:
                joy_press_down_time = now
                joy_long_press_fired = False
                joy_press_was_down = True
            elif joy_is_down and joy_press_was_down:
                if not joy_long_press_fired and now - joy_press_down_time >= 1.5:
                    joy_long_press_fired = True
                    joy_press_times.clear()
                    transfer_mode = not transfer_mode
                    transfer_screen_shown = False
                    if transfer_mode:
                        gallery_active = False
                        splash_active = False
                        preview_active = False
                        print("Transfer mode ON")
                        _transfer_last_activity = time.time()
                        _transfer_dimmed = False
                        subprocess.Popen(["sudo", "systemctl", "start", "retrocam-hotspot.service"])
                        subprocess.Popen(["sudo", "systemctl", "start", "retrocam-gallery.service"])
                    else:
                        preview_active = True
                        print("Transfer mode OFF")
                        if _transfer_dimmed:
                            set_backlight(True)
                        _transfer_dimmed = False
                        _idle_last_activity = time.time()
                        _idle_dimmed = False
                        subprocess.Popen(["sudo", "systemctl", "stop", "retrocam-hotspot.service"])
                        subprocess.Popen(["sudo", "systemctl", "stop", "retrocam-gallery.service"])
            elif not joy_is_down and joy_press_was_down:
                joy_press_was_down = False
                if not joy_long_press_fired and now - joy_press_down_time > 0.02:
                    joy_press_times.append(now)
                    joy_press_times[:] = [t for t in joy_press_times if now - t < TRIPLE_PRESS_WINDOW]
                    if len(joy_press_times) >= 3:
                        joy_press_times.clear()
                        gallery_active = False
                        gallery_confirm_delete = False
                        transfer_mode = False
                        splash_active = True
                        print("Splash activated")
                    elif transfer_mode:
                        pass  
                    elif gallery_active:
                        if gallery_confirm_delete:
                            gallery_confirm_delete = False
                            gallery_needs_update = True
                        else:
                            gallery_active = False
                            preview_active = True
                            print("Gallery closed")
                    else:
                        gallery_images = get_gallery_images()
                        if gallery_images:
                            gallery_index = len(gallery_images) - 1
                            gallery_active = True
                            preview_active = False
                            gallery_needs_update = True
                            print(f"Gallery opened ({len(gallery_images)} images)")
                        else:
                            gallery_empty_message_time = time.time()
                            print("Gallery empty")

            if transfer_mode:
                time.sleep(0.05)
                continue
                
            # WAKE/TOGGLE LOGIC FOR PREVIEW BUTTON
            prev_is_down = not GPIO.input(BUTTON_PREVIEW)
            if prev_is_down and not prev_was_down:
                prev_was_down = True
                prev_down_time = now
                prev_long_fired = False
                # Wake Up Check
                if not preview_active and not gallery_active and not transfer_mode:
                    preview_active = True
                    prev_long_fired = True
                    isp_changed = True
                    print("👁 WAKE UP (PREV)")
            elif prev_is_down and prev_was_down:
                if not prev_long_fired and (now - prev_down_time >= PREV_LONG_THRESHOLD):
                    prev_long_fired = True
                    if preview_active:
                        preview_active = False
                        print("👁 DISPLAY OFF")
            elif not prev_is_down and prev_was_down:
                prev_was_down = False
                if not prev_long_fired:
                    if gallery_active and gallery_confirm_delete:
                        gallery_confirm_delete = False
                        gallery_needs_update = True
                    elif preview_active and not capturing:
                        active_setting_index = (active_setting_index + 1) % 4
                        print(f"UI Toggle state: {active_setting_index}")

            cap_is_down = not GPIO.input(BUTTON_CAPTURE)

            if gif_recording:
                if cap_is_down and not cap_was_down:
                    gif_cancel_requested = True
                    cap_long_fired = True
                    cap_down_time = now
                    print("🚫 GIF cancel")
                cap_was_down = cap_is_down

            elif cap_is_down and not cap_was_down:
                cap_was_down = True
                cap_down_time = now
                cap_long_fired = False
                # Wake Up Check
                if not preview_active and not gallery_active and not transfer_mode:
                    preview_active = True
                    cap_long_fired = True 
                    isp_changed = True
                    print("👁 WAKE UP (CAP)")

            elif cap_is_down and cap_was_down:
                if (not cap_long_fired and now - cap_down_time >= CAP_LONG_THRESHOLD
                        and preview_active and not capturing and not gif_recording
                        and not gallery_active):
                    cap_long_fired = True
                    gif_mode = not gif_mode
                    gif_mode_label_time = now
                    print(f"GIF mode {'ON' if gif_mode else 'OFF'}")

            elif not cap_is_down and cap_was_down:
                cap_was_down = False
                if cap_long_fired:
                    last_capture = now
                elif now - last_capture > debounce:
                    last_capture = now
                    if gallery_active:
                        if gallery_confirm_delete:
                            gallery_confirm_delete = False
                            gallery_needs_update = True
                        else:
                            gallery_active = False
                            preview_active = True
                            print("Gallery closed")
                    elif preview_active and not capturing and not gif_recording:
                        try:
                            check_path = GALLERY_DIR if os.path.exists(GALLERY_DIR) else os.path.dirname(GALLERY_DIR)
                            stat = os.statvfs(check_path)
                            free_bytes = stat.f_bavail * stat.f_bsize
                            if free_bytes < 20 * 1024 * 1024:
                                no_space_message_time = time.time()
                                print("✗ No space in card")
                            elif gif_mode:
                                gif_record_requested = True
                                print("🎞 GIF RECORD")
                            else:
                                capture_requested = True
                                print("📸 CAPTURE")
                        except:
                            if gif_mode:
                                gif_record_requested = True
                                print("🎞 GIF RECORD")
                            else:
                                capture_requested = True
                                print("📸 CAPTURE")

            if gallery_active and gallery_images:
                left_pressed = not GPIO.input(BUTTON_LEFT)
                right_pressed = not GPIO.input(BUTTON_RIGHT)

                if left_pressed:
                    if gallery_confirm_delete:
                        gallery_confirm_delete = False
                        gallery_needs_update = True
                        left_held_since = now  
                    elif left_held_since == 0:
                        left_held_since = now
                        gallery_index = (gallery_index - 1) % len(gallery_images)
                        gallery_needs_update = True
                        last_scroll_time = now
                    elif now - left_held_since > HOLD_THRESHOLD:
                        if now - last_scroll_time > FAST_INTERVAL:
                            gallery_index = (gallery_index - 1) % len(gallery_images)
                            gallery_needs_update = True
                            last_scroll_time = now
                else:
                    left_held_since = 0

                if right_pressed:
                    if gallery_confirm_delete:
                        gallery_confirm_delete = False
                        gallery_needs_update = True
                        right_held_since = now
                    elif right_held_since == 0:
                        right_held_since = now
                        gallery_index = (gallery_index + 1) % len(gallery_images)
                        gallery_needs_update = True
                        last_scroll_time = now
                    elif now - right_held_since > HOLD_THRESHOLD:
                        if now - last_scroll_time > FAST_INTERVAL:
                            gallery_index = (gallery_index + 1) % len(gallery_images)
                            gallery_needs_update = True
                            last_scroll_time = now
                else:
                    right_held_since = 0

            elif preview_active and not capturing:
                # Setting Adjustments (Left/Right)
                if not GPIO.input(BUTTON_LEFT):
                    if now - last_scroll_time > debounce:
                        last_scroll_time = now
                        if active_setting_index == 0: # AWB
                            awb_mode_index = (awb_mode_index - 1) % len(AWB_MODES)
                            awb_mode_changed = True
                            awb_changed_time = now
                        elif active_setting_index == 1: # EV
                            ev_index = max(0, ev_index - 1)
                        elif active_setting_index == 2: # ISO
                            iso_index = max(0, iso_index - 1)
                        elif active_setting_index == 3: # Shutter
                            shutter_index = max(0, shutter_index - 1)
                        isp_changed = True

                if not GPIO.input(BUTTON_RIGHT):
                    if now - last_scroll_time > debounce:
                        last_scroll_time = now
                        if active_setting_index == 0: # AWB
                            awb_mode_index = (awb_mode_index + 1) % len(AWB_MODES)
                            awb_mode_changed = True
                            awb_changed_time = now
                        elif active_setting_index == 1: # EV
                            ev_index = min(len(EV_VALUES)-1, ev_index + 1)
                        elif active_setting_index == 2: # ISO
                            iso_index = min(len(ISO_VALUES)-1, iso_index + 1)
                        elif active_setting_index == 3: # Shutter
                            shutter_index = min(len(SHUTTER_VALUES)-1, shutter_index + 1)
                        isp_changed = True

            time.sleep(0.02)

        except Exception as e:
            print(f"Button error: {e}")
            time.sleep(0.1)

preview_active = True
capture_requested = False
exit_requested = False
camera_started = False
first_preview_frame_pending = True
cold_start_pending = True   
capturing = False
capture_dot_time = 0
gallery_active = False
gallery_index = 0
gallery_images = []
gallery_needs_update = False
gallery_confirm_delete = False
gallery_empty_message_time = 0
no_space_message_time = 0
splash_active = False
awb_mode_changed = False
awb_changed_time = 0
_awb_last_label = None     
_awb_pop_start = 0.0
filter_index = FILTERS.index("Film Standard")
saving_active = 0
_save_active_lock = threading.Lock()
filter_label_time = 0
isp_changed = False
gif_mode = False              
gif_record_requested = False  
gif_recording = False         
gif_cancel_requested = False  
gif_mode_label_time = 0       
_gif_anim_frames = []         
_gif_anim_index = 0
_gif_anim_last = 0.0
_gif_anim_file = None         
transfer_mode = False
transfer_screen_shown = False
_transfer_last_refresh = 0
_transfer_last_activity = 0.0
_transfer_dimmed = False
_idle_last_activity = 0.0
_idle_dimmed = False
IDLE_DIM_TIMEOUT = 90.0

def main():
    log("main() called")
    global preview_active, capture_requested, exit_requested, camera_started
    global capturing, capture_dot_time, config_cache
    global gallery_active, gallery_index, gallery_images, gallery_needs_update, gallery_confirm_delete, gallery_empty_message_time
    global awb_mode_changed, awb_changed_time, no_space_message_time, splash_active
    global _awb_last_label, _awb_pop_start
    global filter_index, filter_label_time, isp_changed, saving_active
    global transfer_mode, transfer_screen_shown, _transfer_last_refresh, _transfer_last_activity, _transfer_dimmed
    global _idle_last_activity, _idle_dimmed
    global first_preview_frame_pending, cold_start_pending
    global gif_mode, gif_record_requested, gif_recording, gif_mode_label_time
    global _gif_anim_frames, _gif_anim_index, _gif_anim_last, _gif_anim_file
    global awb_mode_index, ev_index, iso_index, shutter_index, active_setting_index

    # Initialize AWB Index safely
    awb_mode_index = AWB_MODES.index(next(m for m in AWB_MODES if m[1] == "Daylight"))

    gc.disable()

    print("=" * 50)
    print("CAMERA - FAST & ROBUST")
    print("=" * 50)

    log("Initializing display...")
    init_display()
    clear_display()        
    show_splash()          
    set_backlight(True)    
    log("Display ready - showing splash screen")

    log("Waiting for heavy-preload thread (picamera2 + PIL)...")
    print("Initializing camera...")
    _preload_thread.join()
    from picamera2 import Picamera2  
    log("Picamera2 imported")

    _cam_t0 = time.time()
    while not Picamera2.global_camera_info():
        if time.time() - _cam_t0 > 30:
            log("WARN: camera enumeration timed out")
            break
        time.sleep(0.05)
    log("camera enumerated")

    picam2 = Picamera2()
    config_cache = CameraConfigCache(picam2)

    print("\n" + "=" * 50)
    print("FEATURES:")
    print("✓ Enhanced black levels")
    print("✓ LANDSCAPE 4:3 PREVIEW ENABLED")
    print("✓ LIVE EV/ISO/SHUTTER CONTROLS")
    print("✓ REAL-TIME AUTO METADATA METERING")
    print("✓ STRICT MANUAL HIERARCHY LOGIC")
    print(f"✓ KEY1 (GPIO {BUTTON_CAPTURE}): Capture / Wake Display")
    print(f"✓ KEY2 (GPIO {BUTTON_PREVIEW}): Tap=Cycle Setting, Hold=Display ON/OFF")
    print(f"✓ Center button: Hold = Transfer Mode, Tap = Splash")
    print(f"✓ Left/Right buttons: Adjust highlighted setting")
    print(f"✓ Up/Down buttons: Cycle film filters")
    print("=" * 50 + "\n")

    button_thread = threading.Thread(target=button_handler, daemon=True)
    button_thread.start()

    def _warm_overlays():
        for f in FILTERS:
            try:
                get_filter_indicator(f)
            except Exception as e:
                print(f"warm indicator error: {e}")
        try:
            get_gif_mode_indicator(0)   
        except Exception as e:
            print(f"warm gif pill error: {e}")
    threading.Thread(target=_warm_overlays, daemon=True).start()

    frame_count = 0
    last_fps_report = time.time()
    _idle_last_activity = time.time()

    try:
        while not exit_requested:

            if not transfer_mode and not splash_active:
                if not _idle_dimmed and time.time() - _idle_last_activity > IDLE_DIM_TIMEOUT:
                    _idle_dimmed = True
                    set_backlight_brightness(8)

            if splash_active:
                if camera_started:
                    with camera_lock:
                        if camera_started:
                            picam2.stop()
                            camera_started = False
                            capture_dot_time = 0
                set_backlight(True)
                show_splash()
                time.sleep(0.05)

            elif transfer_mode:
                if camera_started:
                    with camera_lock:
                        if camera_started:
                            picam2.stop()
                            camera_started = False
                            capture_dot_time = 0
                if not transfer_screen_shown:
                    transfer_screen_shown = True
                    _transfer_last_refresh = 0
                    _transfer_last_activity = time.time()
                    _transfer_dimmed = False
                    set_backlight(True)
                any_pressed = (
                    not GPIO.input(BUTTON_CAPTURE) or
                    not GPIO.input(BUTTON_PREVIEW) or
                    not GPIO.input(BUTTON_UP) or
                    not GPIO.input(BUTTON_DOWN) or
                    not GPIO.input(BUTTON_LEFT) or
                    not GPIO.input(BUTTON_RIGHT) or
                    not GPIO.input(BUTTON_PRESS)
                )
                if any_pressed:
                    if _transfer_dimmed:
                        _transfer_dimmed = False
                        set_backlight(True)
                        _transfer_last_refresh = 0
                    _transfer_last_activity = time.time()
                elif not _transfer_dimmed and time.time() - _transfer_last_activity > 30:
                    _transfer_dimmed = True
                    set_backlight_brightness(8)
                if time.time() - _transfer_last_refresh >= 0.5:
                    _transfer_last_refresh = time.time()
                    show_transfer_mode_screen()
                time.sleep(0.1)

            elif gallery_active:
                if camera_started:
                    with camera_lock:
                        if camera_started:
                            picam2.stop()
                            camera_started = False
                            capture_dot_time = 0

                if gallery_needs_update and gallery_images:
                    gallery_needs_update = False
                    idx = gallery_index  
                    total = len(gallery_images)
                    set_backlight(True)
                    is_gif = gallery_images[idx].lower().endswith(".gif")
                    if is_gif and not gallery_confirm_delete:
                        _gif_anim_frames = start_gif_gallery_load(gallery_images[idx], idx + 1, total)
                        _gif_anim_file = gallery_images[idx]
                        _gif_anim_index = 0
                        _gif_anim_last = time.time()
                        if _gif_anim_frames:
                            display_image(_gif_anim_frames[0])
                        else:
                            display_gallery_image(gallery_images[idx], idx + 1, total, False)
                    else:
                        _gif_anim_frames = []
                        _gif_anim_file = None
                        display_gallery_image(gallery_images[idx], idx + 1, total, gallery_confirm_delete)
                    print(f"Gallery: {idx + 1}/{total}")

                if (_gif_anim_frames and not gallery_confirm_delete
                        and gallery_images and gallery_index < len(gallery_images)
                        and gallery_images[gallery_index] == _gif_anim_file):
                    if time.time() - _gif_anim_last >= GIF_FRAME_MS / 1000.0:
                        _gif_anim_last = time.time()
                        _gif_anim_index = (_gif_anim_index + 1) % len(_gif_anim_frames)
                        display_image(_gif_anim_frames[_gif_anim_index])

                time.sleep(0.02)

            elif preview_active and not capturing:
                if not camera_started:
                    with camera_lock:
                        set_backlight(True)
                        picam2.configure(config_cache.preview_config)
                        picam2.start()
                        camera_started = True
                        first_preview_frame_pending = True
                        isp_changed = True
                        awb_mode_changed = True
                        _idle_last_activity = time.time()
                        print("✓ Preview started\n")

                if capture_requested:
                    capture_requested = False
                    capture_dot_time = time.time()
                    threading.Thread(
                        target=capture_full_res,
                        args=(picam2,),
                        daemon=True
                    ).start()

                if gif_record_requested:
                    gif_record_requested = False
                    _idle_last_activity = time.time()
                    record_gif(picam2)
                    continue

                if camera_started and not capturing:
                    try:
                        with camera_lock:
                            if camera_started and not capturing:
                                if isp_changed or awb_mode_changed:
                                    isp_changed = False
                                    awb_mode_changed = False
                                    picam2.set_controls(get_camera_controls())
                                    
                                req = picam2.capture_request()
                                preview_image = req.make_image("main")
                                metadata = req.get_metadata()
                                req.release()
                                
                                # MIRRORED FOR LANDSCAPE
                                preview_image = preview_image.transpose(Image.FLIP_LEFT_RIGHT)
                                
                                if first_preview_frame_pending and cold_start_pending:
                                    cold_start_pending = False
                                    first_preview_frame_pending = False
                                    if preview_image.size != (320, 240): 
                                        preview_image = preview_image.resize((320, 240), Image.LANCZOS)
                                    display_image(preview_image)
                                    log("First preview frame displayed")
                                    frame_count += 1
                                    continue
                                first_preview_frame_pending = False
                                preview_image = apply_filter(preview_image)
                                if preview_image.size != (320, 240): 
                                    preview_image = preview_image.resize((320, 240), Image.LANCZOS)
                                if capture_dot_time > 0 and (time.time() - capture_dot_time) >= 2.0:
                                    capture_dot_time = 0

                                from PIL import ImageDraw
                                font_hud = load_font(20) # HUD Text is 20% smaller
                                font_awb_full  = load_font(25)
                                font_awb_abbr  = load_font(24)
                                tmp_draw = ImageDraw.Draw(preview_image)

                                # Pull real-time data to show exact live numbers in Auto Mode
                                rt_iso = nearest_standard_iso(metadata.get("AnalogueGain", 1.0))
                                rt_exp = metadata.get("ExposureTime", 10000)
                                rt_shutter = nearest_standard_shutter(rt_exp) if rt_exp > 0 else "?"

                                awb_switching = time.time() - awb_changed_time < 1.0
                                awb_label = (AWB_MODES[awb_mode_index][1] if awb_switching
                                             else AWB_MODES[awb_mode_index][2])
                                             
                                is_manual_iso = (ISO_VALUES[iso_index] != 0)
                                is_manual_shutter = (SHUTTER_VALUES[shutter_index] != 0)
                                is_full_manual = is_manual_iso and is_manual_shutter

                                iso_label = f"ISO {ISO_VALUES[iso_index]}" if is_manual_iso else f"ISO {rt_iso} (A)"
                                shutter_label = SHUTTER_LABELS[shutter_index] if is_manual_shutter else f"{rt_shutter} (A)"
                                ev_label = "EV [ M ]" if is_full_manual else f"EV {EV_VALUES[ev_index]:+.1f}"

                                font_awb = font_awb_full if awb_switching else font_awb_abbr
                                awb_font_size = 25 if awb_switching else 24

                                b_awb = tmp_draw.textbbox((0, 0), awb_label, font=font_awb)
                                b_iso = tmp_draw.textbbox((0, 0), iso_label, font=font_hud)
                                b_ev = tmp_draw.textbbox((0, 0), ev_label, font=font_hud)
                                b_sh = tmp_draw.textbbox((0, 0), shutter_label, font=font_hud)

                                ax, ay = 15, 15 - b_awb[1]
                                ix = 15
                                ex = 15
                                ey = 120 - (b_ev[3] - b_ev[1]) // 2  # Left Border & Vertically Centered
                                sx = 320 - 15 - (b_sh[2] - b_sh[0])

                                hud_bottom_y = 240 - 15 - max(b_iso[3] - b_iso[1], b_sh[3] - b_sh[1]) 
                                iy = sy = hud_bottom_y

                                if awb_label != _awb_last_label:
                                    _awb_last_label = awb_label
                                    _awb_pop_start = time.time()
                                awb_popping = time.time() - _awb_pop_start < AWB_POP_DUR

                                centre_msg = None
                                centre_msg_start = 0.0
                                centre_msg_dur = 1.5
                                if gallery_empty_message_time > 0 and time.time() - gallery_empty_message_time < 1.0:
                                    centre_msg = "No image in card"
                                    centre_msg_start, centre_msg_dur = gallery_empty_message_time, 1.0
                                elif gallery_empty_message_time > 0:
                                    gallery_empty_message_time = 0
                                if centre_msg is None and no_space_message_time > 0 and time.time() - no_space_message_time < 1.0:
                                    centre_msg = "No space in card"
                                    centre_msg_start, centre_msg_dur = no_space_message_time, 1.0
                                elif centre_msg is None and no_space_message_time > 0:
                                    no_space_message_time = 0
                                if (centre_msg is None and filter_label_time > 0
                                        and time.time() - filter_label_time < 1.5
                                        and gallery_empty_message_time == 0
                                        and no_space_message_time == 0):
                                    centre_msg = FILTERS[filter_index]
                                    centre_msg_start, centre_msg_dur = filter_label_time, 1.5
                                elif centre_msg is None and filter_label_time > 0:
                                    filter_label_time = 0
                                if (centre_msg is None and gif_mode_label_time > 0
                                        and time.time() - gif_mode_label_time < 1.5):
                                    centre_msg = "GIF Mode" if gif_mode else "Photo Mode"
                                    centre_msg_start, centre_msg_dur = gif_mode_label_time, 1.5
                                elif centre_msg is None and gif_mode_label_time > 0:
                                    gif_mode_label_time = 0

                                gif_phase_mod = (int(time.time() * _GIF_PILL_MARCH_HZ) % _GIF_PILL_PHASES) if gif_mode else -1
                                pill_popping = (filter_label_time > 0
                                                and time.time() - filter_label_time < PILL_POP_DUR)

                                hud_key = (awb_label, iso_label, ev_label, shutter_label,
                                           FILTERS[filter_index], gif_phase_mod, pill_popping, awb_popping, active_setting_index)
                                           
                                if _hud_overlay_cache["key"] == hud_key:
                                    overlay = _hud_overlay_cache["img"]
                                else:
                                    awb_sh = get_cached_shadow("awb", awb_label, ax, ay, font_awb)
                                    iso_sh = get_cached_shadow("iso", iso_label, ix, iy, font_hud)
                                    ev_sh = get_cached_shadow("ev", ev_label, ex, ey, font_hud)
                                    sh_sh = get_cached_shadow("shutter", shutter_label, sx, sy, font_hud)
                                    
                                    overlay = Image.alpha_composite(iso_sh, sh_sh)
                                    overlay = Image.alpha_composite(overlay, ev_sh)
                                    if not awb_popping:
                                        overlay = Image.alpha_composite(overlay, awb_sh)
                                    if not pill_popping:
                                        overlay = Image.alpha_composite(overlay, get_filter_indicator(FILTERS[filter_index]))
                                    if gif_mode:
                                        overlay = Image.alpha_composite(overlay, get_gif_mode_indicator(gif_phase_mod))
                                    
                                    _hud_overlay_cache["key"] = hud_key
                                    _hud_overlay_cache["img"] = overlay

                                preview_image = preview_image.convert("RGBA")
                                preview_image = Image.alpha_composite(preview_image, overlay)
                                preview_image = preview_image.convert("RGB")

                                draw_hud = ImageDraw.Draw(preview_image)
                                if not awb_popping:
                                    draw_hud.text((ax, ay), awb_label, font=font_awb, fill=(255, 255, 255))
                                draw_hud.text((ix, iy), iso_label, font=font_hud, fill=(255, 255, 255))
                                draw_hud.text((ex, ey), ev_label, font=font_hud, fill=(255, 255, 255))
                                draw_hud.text((sx, sy), shutter_label, font=font_hud, fill=(255, 255, 255))

                                # Draw pill outline over active setting
                                if active_setting_index == 0 and not awb_popping:
                                    draw_pill_outline(draw_hud, ax, ay, b_awb)
                                elif active_setting_index == 1:
                                    draw_pill_outline(draw_hud, ex, ey, b_ev)
                                elif active_setting_index == 2:
                                    draw_pill_outline(draw_hud, ix, iy, b_iso)
                                elif active_setting_index == 3:
                                    draw_pill_outline(draw_hud, sx, sy, b_sh)

                                if saving_active > 0:
                                    sp_r  = 7
                                    sp_cx = 160
                                    sp_cy = hud_bottom_y + (b_iso[1] + b_iso[3]) // 2
                                    sp_a  = int(time.time() * 360) % 360
                                    sp_box   = [sp_cx-sp_r,   sp_cy-sp_r,   sp_cx+sp_r,   sp_cy+sp_r]
                                    sp_box_s = [sp_cx-sp_r+1, sp_cy-sp_r+1, sp_cx+sp_r+1, sp_cy+sp_r+1]
                                    draw_hud.arc(sp_box_s, start=sp_a, end=sp_a+270, fill=(0, 0, 0),       width=2)
                                    draw_hud.arc(sp_box,   start=sp_a, end=sp_a+270, fill=(255, 255, 255), width=2)

                                if awb_popping:
                                    preview_image = render_pill_pop(
                                        preview_image, get_awb_sprite(awb_label, awb_font_size),
                                        _awb_pop_scale(time.time() - _awb_pop_start, awb_switching))

                                if pill_popping:
                                    preview_image = render_pill_pop(
                                        preview_image, get_filter_indicator(FILTERS[filter_index]),
                                        _pill_pop_scale(time.time() - filter_label_time))

                                if centre_msg is not None:
                                    preview_image = render_centre_msg(
                                        preview_image, centre_msg,
                                        time.time() - centre_msg_start, centre_msg_dur)

                                display_image(preview_image)
                                frame_count += 1
                                if time.time() - last_fps_report >= 5.0:
                                    elapsed = time.time() - last_fps_report
                                    fps = frame_count / elapsed
                                    print(f"📊 {fps:.1f} fps")
                                    frame_count = 0
                                    last_fps_report = time.time()
                    except Exception as e:
                        print(f"Preview error: {e}")
                        time.sleep(0.1)

                time.sleep(0.001)

            else:
                if camera_started and not capturing:
                    with camera_lock:
                        if camera_started:
                            picam2.stop()
                            camera_started = False
                            set_backlight(False)
                            clear_display()
                            capture_dot_time = 0
                            print("✓ Preview stopped\n")
                time.sleep(0.05)

    except KeyboardInterrupt:
        print("\n\nShutting down...")
        exit_requested = True

    finally:
        with camera_lock:
            if camera_started:
                picam2.stop()
        set_backlight(False)
        GPIO.cleanup()
        spi.close()
        gc.enable()
        print("✓ Shutdown complete!")
if __name__ == "__main__":
    main()
/* optocam_hud.h — exact port of the Python app's HUD, sprites and filters.
 * All layout numbers, easing curves and timings from optocamzero.py (see
 * PORT-SPEC.md). Uses optocam_render.h. Sprites cached like the original.
 */
#pragma once
#include "optocam_render.h"
#include <ctime>

static TextEngine g_te;
inline double now_s() { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return ts.tv_sec + ts.tv_nsec / 1e9; }

/* ---------- filters (exact _BASE_CURVES / _CHANNEL_LUTS) ---------- */
static const char *FILTERS[9] = {"Film Standard","Punch","B&W","Deep","Sand","Eterna","TRI-X","Cutout","No Filter"};
struct FilterLUTs { uint8_t r[256], g[256], b[256]; bool trix=false, cutout=false, identity=false; };
static FilterLUTs g_luts[9];
static uint8_t g_trix_lut[256][3], g_cutout_lut[256];
struct IspSet { float sat, con, bri; };
static const IspSet FILM_ISP[9] = {                     /* order matches FILTERS */
    {0.85f,1,0},{1.3f,1,-0.05f},{0,1,0},{0.6f,1,0},{0.45f,1,0},{0.75f,1,0},{1,1,0},{0,1,0},{1,1,0}};
static const int GRAIN[9] = {18,22,27,22,22,15,15,22,0};

inline void _make_curve(uint8_t *out, std::initializer_list<std::pair<int,int>> pts) {
    std::vector<std::pair<int,int>> p(pts);
    for (int x = 0; x < 256; x++) {
        size_t i = 1; while (i < p.size() - 1 && x > p[i].first) i++;
        float t = (float)(x - p[i-1].first) / (p[i].first - p[i-1].first);
        float v = p[i-1].second + t * (p[i].second - p[i-1].second);
        out[x] = (uint8_t)std::clamp(v, 0.0f, 255.0f);
    }
}
inline void filters_init() {
    uint8_t bw[256], pu[256], sa[256], de[256], et[256], fs[256];
    _make_curve(bw, {{0,0},{64,16},{128,160},{192,242},{255,255}});
    _make_curve(pu, {{0,0},{64,52},{128,148},{192,212},{255,242}});
    _make_curve(sa, {{0,0},{64,50},{128,132},{192,205},{255,255}});
    _make_curve(de, {{0,30},{64,70},{128,152},{192,222},{255,255}});
    _make_curve(et, {{0,30},{64,78},{128,128},{192,172},{255,215}});
    _make_curve(fs, {{0,18},{64,55},{128,140},{192,210},{255,252}});
    auto mk = [](FilterLUTs &f, uint8_t *c, float mr, float mg, float mb) {
        for (int i = 0; i < 256; i++) {
            f.r[i] = (uint8_t)std::clamp(c[i]*mr, 0.f, 255.f);
            f.g[i] = (uint8_t)std::clamp(c[i]*mg, 0.f, 255.f);
            f.b[i] = (uint8_t)std::clamp(c[i]*mb, 0.f, 255.f);
        }
    };
    mk(g_luts[0], fs, 0.95f, 1.02f, 1.08f);             /* Film Standard */
    mk(g_luts[1], pu, 1.05f, 1.02f, 1.0f);              /* Punch (B special below) */
    for (int i = 0; i < 256; i++)
        g_luts[1].b[i] = (uint8_t)std::clamp(pu[i] + std::max(0.f, 65.f*(1.f - i/105.f)), 0.f, 255.f);
    mk(g_luts[2], bw, 1, 1, 1);                         /* B&W */
    mk(g_luts[3], de, 0.55f, 0.70f, 1.35f);             /* Deep */
    mk(g_luts[4], sa, 1.08f, 0.92f, 0.55f);             /* Sand */
    mk(g_luts[5], et, 0.96f, 1.00f, 1.05f);             /* Eterna */
    g_luts[6].trix = true;                              /* TRI-X */
    for (int i = 0; i < 256; i++) {
        float t = i / 255.0f; float c[3];
        const float MID[3]={242,183,8}, HI[3]={35,155,60};
        if (t <= 0.18f) { c[0]=c[1]=c[2]=0; }
        else if (t <= 0.38f) { float u=(t-0.18f)/0.20f; for(int k=0;k<3;k++) c[k]=MID[k]*u; }
        else { float u=(t-0.38f)/0.62f; for(int k=0;k<3;k++) c[k]=MID[k]+(HI[k]-MID[k])*u; }
        for (int k = 0; k < 3; k++) g_trix_lut[i][k] = (uint8_t)std::clamp(c[k], 0.f, 255.f);
    }
    g_luts[7].cutout = true;                            /* Cutout */
    for (int i = 0; i < 256; i++) g_cutout_lut[i] = i < 65 ? 0 : i < 130 ? 128 : 255;
    g_luts[8].identity = true;                          /* No Filter */
}
/* apply in place on RGB triplets (r,g,b order), n pixels */
inline void filter_apply(uint8_t *rgb, size_t n, int idx) {
    FilterLUTs &f = g_luts[idx];
    if (f.identity) return;
    if (f.trix) {
        for (size_t i = 0; i < n; i++) {
            uint8_t *p = rgb + i*3;
            unsigned luma = (p[0]*299u + p[1]*587u + p[2]*114u + 500u) / 1000u;
            p[0]=g_trix_lut[luma][0]; p[1]=g_trix_lut[luma][1]; p[2]=g_trix_lut[luma][2];
        }
        return;
    }
    if (f.cutout) {
        for (size_t i = 0; i < n; i++) {
            uint8_t *p = rgb + i*3;
            /* PIL convert('L') = (299R+587G+114B)/1000 */
            uint8_t v = g_cutout_lut[(p[0]*299u + p[1]*587u + p[2]*114u) / 1000u];
            p[0]=p[1]=p[2]=v;
        }
        return;
    }
    for (size_t i = 0; i < n; i++) {
        uint8_t *p = rgb + i*3;
        p[0]=f.r[p[0]]; p[1]=f.g[p[1]]; p[2]=f.b[p[2]];
    }
}
/* grain: 1024x1024 int16 table per intensity, fixed seed, random offset per use */
inline int16_t *grain_table(int intensity) {
    static std::map<int, std::vector<int16_t>> tabs;
    auto it = tabs.find(intensity);
    if (it == tabs.end()) {
        std::vector<int16_t> t(1024*1024);
        uint64_t st = 0x9E3779B97F4A7C15ull;            /* fixed seed */
        for (auto &v : t) {
            st ^= st << 13; st ^= st >> 7; st ^= st << 17;
            v = (int16_t)((int)(st % (2*intensity + 1)) - intensity);
        }
        it = tabs.emplace(intensity, std::move(t)).first;
    }
    return it->second.data();
}
inline void grain_apply(uint8_t *rgb, int w, int h, int intensity) {
    if (!intensity) return;
    int16_t *tab = grain_table(intensity);
    unsigned dy = rand() & 1023, dx = rand() & 1023;
    for (int y = 0; y < h; y++) {
        const int16_t *row = tab + ((y + dy) & 1023) * 1024;
        uint8_t *p = rgb + (size_t)y * w * 3;
        for (int x = 0; x < w; x++) {
            int gv = row[(x + dx) & 1023];
            for (int c = 0; c < 3; c++) {
                int v = p[x*3+c] + gv;
                p[x*3+c] = (uint8_t)(v < 0 ? 0 : v > 255 ? 255 : v);
            }
        }
    }
}

/* ---------- ISO / shutter tables ---------- */
static const int STD_ISO[13] = {100,125,160,200,250,320,400,500,640,800,1000,1250,1600};
static const struct { int us; const char *lbl; } STD_SHUTTER[36] = {
    {125,"1/8000"},{156,"1/6400"},{200,"1/5000"},{250,"1/4000"},{313,"1/3200"},{400,"1/2500"},
    {500,"1/2000"},{625,"1/1600"},{800,"1/1250"},{1000,"1/1000"},{1250,"1/800"},{1563,"1/640"},
    {2000,"1/500"},{2500,"1/400"},{3125,"1/320"},{4000,"1/250"},{5000,"1/200"},{6250,"1/160"},
    {8000,"1/125"},{10000,"1/100"},{12500,"1/80"},{16667,"1/60"},{20000,"1/50"},{25000,"1/40"},
    {33333,"1/30"},{40000,"1/25"},{50000,"1/20"},{66667,"1/15"},{100000,"1/10"},{125000,"1/8"},
    {166667,"1/6"},{200000,"1/5"},{250000,"1/4"},{333333,"1/3"},{500000,"1/2"},{1000000,"1\""}};
inline const char *nearest_iso(float gain, char *buf) {
    float iso = gain * 100; int best = STD_ISO[0];
    for (int s : STD_ISO) if (std::fabs(s - iso) < std::fabs(best - iso)) best = s;
    sprintf(buf, "%d", best); return buf;
}
inline const char *nearest_shutter(int us) {
    int bi = 0;
    for (int i = 0; i < 36; i++) if (std::abs(STD_SHUTTER[i].us - us) < std::abs(STD_SHUTTER[bi].us - us)) bi = i;
    return STD_SHUTTER[bi].lbl;
}

/* ---------- AWB modes ---------- */
/* libcamera AwbModeEnum: 1=Incandescent 2=Tungsten 3=Fluorescent 4=Indoor
 * 5=Daylight 6=Cloudy. The original app's table was OFF BY ONE (sent 4 =
 * Indoor for "Daylight" → AWB locked to 3000-5000K under real daylight →
 * blue images). Numbers fixed to match the labels' obvious intent. */
/* curated lineup (July 2026, user-approved): Auto replaces the redundant
 * warm presets (Incandescent≈Tungsten, Fluorescent⊂Indoor). Same 5 slots —
 * UI/joystick behaviour unchanged. */
static const struct { int mode; const char *full, *abbr; } AWB_MODES[5] = {
    {5,"Daylight","DAY"},{6,"Cloudy","CLD"},{4,"Indoor","IND"},{2,"Tungsten","TNG"},{0,"Auto","AWB"}};
#define AWB_DEFAULT_IDX 0   /* Daylight */

/* ---------- shadow-text sprite (white text + blurred black shadow) ---------- */
inline void gaussian_blur_roi(Img &img, float radius) {
    /* blur only the inked region (+3r margin) — text is tiny vs the canvas */
    int x0=img.w, y0=img.h, x1=-1, y1=-1;
    for (int y = 0; y < img.h; y++) for (int x = 0; x < img.w; x++)
        if (img.at(x,y)[3]) { x0=std::min(x0,x); y0=std::min(y0,y); x1=std::max(x1,x); y1=std::max(y1,y); }
    if (x1 < 0) return;
    int m = (int)(3*radius) + 2;
    x0=std::max(0,x0-m); y0=std::max(0,y0-m); x1=std::min(img.w-1,x1+m); y1=std::min(img.h-1,y1+m);
    Img sub(x1-x0+1, y1-y0+1);
    for (int y = 0; y < sub.h; y++) memcpy(sub.at(0,y), img.at(x0,y0+y), (size_t)sub.w*4);
    gaussian_blur(sub, radius);
    for (int y = 0; y < sub.h; y++) memcpy(img.at(x0,y0+y), sub.at(0,y), (size_t)sub.w*4);
}
inline Img text_shadow_sprite(int fontsz, const std::string &s, int x, int y, float blur = 4) {
    Img sh(240, 240);
    Font *f = g_te.font(fontsz);
    g_te.draw(sh, f, x, y, s, 0, 0, 0, 250);
    gaussian_blur_roi(sh, blur);
    return sh;
}
inline Img &cached_shadow(const char *slot, const std::string &text, int fontsz, int x, int y) {
    static std::map<std::string, std::pair<std::string, Img>> cache;
    auto &e = cache[slot];
    std::string k = text + "@" + std::to_string(fontsz) + "," + std::to_string(x) + "," + std::to_string(y);
    if (e.first != k) { e.first = k; e.second = text_shadow_sprite(fontsz, text, x, y); }
    return e.second;
}

/* ---------- filter indicator (3x supersampled circle/pill) ----------
 * `te` defaults to the shared engine; the boot-time warmer passes its OWN
 * TextEngine so these ~200ms builds run off g_render_mtx (they stuttered
 * the fresh preview otherwise). */
inline Img build_filter_indicator(const std::string &name, TextEngine &te = g_te) {
    const int S = 3, big = 240*S, pad = 10*S, r = 17*S;
    float cx = big - pad - r, cy = pad + r;
    Font *f = te.font(24*S);
    Img shadow(big, big), white(big, big);
    std::string label; bool pill = false;
    if (name == "B&W") { label = "B&W"; pill = true; }
    else if (name == "TRI-X") { label = "TX"; pill = true; }
    else if (name == "Film Standard") { label = "FS"; pill = true; }
    else label = name.substr(0, 1);
    int bx0,by0,bx1,by1; te.textbbox(f, label, bx0,by0,bx1,by1);
    if (pill) {
        int hpad = 10*S, ph = r*2, pw = (bx1-bx0) + hpad*2;
        float x1 = big - pad, y0v = pad, x0v = x1 - pw, y1v = y0v + ph, cr = ph/2.0f;
        int pcx = (int)(x0v + x1)/2, pcy = (int)(y0v + y1v)/2;
        int tx = pcx - (bx0 + bx1)/2, ty = pcy - (by0 + by1)/2;
        draw_rounded_rect_outline(shadow, x0v, y0v, x1, y1v, cr, 2*S, 0,0,0,250);
        te.draw(shadow, f, tx, ty, label, 0,0,0,250);
        draw_rounded_rect_outline(white, x0v, y0v, x1, y1v, cr, 1*S, 255,255,255,255);
        te.draw(white, f, tx, ty, label, 255,255,255,255);
    } else {
        int tx = (int)cx - (bx0 + bx1)/2, ty = (int)cy - (by0 + by1)/2;
        if (label == "D" || label == "P" || label == "L" || label == "N") tx += S;
        draw_ellipse_outline(shadow, cx-r, cy-r, cx+r, cy+r, 2*S, 0,0,0,250);
        te.draw(shadow, f, tx, ty, label, 0,0,0,250);
        draw_ellipse_outline(white, cx-r, cy-r, cx+r, cy+r, 1*S, 255,255,255,255);
        te.draw(white, f, tx, ty, label, 255,255,255,255);
    }
    gaussian_blur_roi(shadow, 4*S);
    alpha_composite(shadow, white);
    return resize_lanczos(shadow, 240, 240);
}
inline std::map<int, Img> g_filt_ind_cache;   /* guarded by g_render_mtx */
inline Img &filter_indicator(int idx) {
    auto it = g_filt_ind_cache.find(idx);
    if (it == g_filt_ind_cache.end())
        it = g_filt_ind_cache.emplace(idx, build_filter_indicator(FILTERS[idx])).first;
    return it->second;
}

/* ---------- centre message sprite (font 25, centred, blur 4) ---------- */
inline Img &centre_msg_sprite(const std::string &text) {
    static std::map<std::string, Img> cache;
    auto it = cache.find(text);
    if (it != cache.end()) return it->second;
    Font *f = g_te.font(25);
    int x0,y0,x1,y1; g_te.textbbox(f, text, x0,y0,x1,y1);
    int mx = (240 - (x1-x0))/2 - x0, my = (240 - (y1-y0))/2 - y0;
    Img shadow(240,240), white(240,240);
    g_te.draw(shadow, f, mx, my, text, 0,0,0,250);
    gaussian_blur(shadow, 4);
    g_te.draw(white, f, mx, my, text, 255,255,255,255);
    alpha_composite(shadow, white);
    return cache.emplace(text, std::move(shadow)).first->second;
}

/* ---------- easing (exact) ---------- */
inline float ease_out_back(float t, float c1 = 1.9f) {
    float c3 = c1 + 1;
    return 1 + c3*(t-1)*(t-1)*(t-1) + c1*(t-1)*(t-1);
}
inline void ease_centre_msg(float age, float dur, float &alpha, float &scale) {
    const float A_IN=0.12f, A_OUT=0.18f, S_IN=0.18f;
    if (age < A_IN) { float u = 1 - age/A_IN; alpha = 1 - u*u*u; }
    else if (age > dur - A_OUT) { float t = std::max(0.f, (dur-age)/A_OUT); alpha = t*t; }
    else alpha = 1;
    if (age < S_IN) { float t = age/S_IN, u = 1-t; scale = 0.80f + 0.20f*(1 - u*u*u); }
    else scale = 1;
}
inline float pill_pop_scale(float age) {                /* 0.30s */
    float t = std::clamp(age / 0.30f, 0.f, 1.f);
    return 0.90f + 0.10f * ease_out_back(t);
}

/* composite a 240 sprite scaled about the canvas centre (centre msg) */
inline void composite_scaled_centre(Img &base, const Img &sprite, float scale, float alpha) {
    Img tmp = sprite;
    if (std::fabs(scale - 1.0f) > 0.005f) {
        int sw = std::max(1, (int)(240 * scale));
        Img small_ = resize_bilinear(sprite, sw, sw);
        tmp = Img(240, 240);
        int off = (240 - sw) / 2;
        for (int y = 0; y < sw; y++)
            memcpy(tmp.at(off, off + y), small_.at(0, y), (size_t)sw * 4);
    }
    if (alpha < 0.999f) scale_alpha(tmp, alpha);
    alpha_composite(base, tmp);
}
/* composite scaled about the sprite's own bbox centre (pill pop) */
inline void composite_pill_pop(Img &base, const Img &sprite, float scale) {
    if (std::fabs(scale - 1.0f) < 0.005f) { alpha_composite(base, sprite); return; }
    int bx0=240, by0=240, bx1=0, by1=0;
    for (int y = 0; y < 240; y++) for (int x = 0; x < 240; x++)
        if (sprite.at(x, y)[3]) { bx0=std::min(bx0,x); by0=std::min(by0,y); bx1=std::max(bx1,x); by1=std::max(by1,y); }
    if (bx1 < bx0) { alpha_composite(base, sprite); return; }
    float cx = (bx0 + bx1)/2.0f, cy = (by0 + by1)/2.0f;
    int sw = std::max(1, (int)(240 * scale));
    Img rs = resize_bilinear(sprite, sw, sw);
    Img canvas(240, 240);
    int ox = (int)std::round(cx * (1 - scale)), oy = (int)std::round(cy * (1 - scale));
    for (int y = 0; y < sw; y++) {
        int py = oy + y; if (py < 0 || py >= 240) continue;
        for (int x = 0; x < sw; x++) {
            int px = ox + x; if (px < 0 || px >= 240) continue;
            memcpy(canvas.at(px, py), rs.at(x, y), 4);
        }
    }
    alpha_composite(base, canvas);
}

/* ---------- GIF-mode pill: marching dashed capsule, 2 phases at 4Hz ---------- */
inline void _dashed_capsule(Img &img, float x0, float y0, float x1, float y1, float cr,
                            float phase, float dash, float gap, int width,
                            uint8_t r, uint8_t g, uint8_t b, uint8_t a) {
    std::vector<std::pair<float,float>> pts;
    float xa = x0 + cr, xb = x1 - cr, cy = y0 + cr;
    const float step = 2.0f * 3;                       /* step=2.0 at S=3 */
    for (float d = xa; d <= xb; d += step) pts.push_back({d, y0});
    for (float t = -(float)M_PI/2; t <= (float)M_PI/2; t += step/cr) pts.push_back({xb + cr*std::cos(t), cy + cr*std::sin(t)});
    for (float d = xb; d >= xa; d -= step) pts.push_back({d, y1});
    for (float t = (float)M_PI/2; t <= 3*(float)M_PI/2; t += step/cr) pts.push_back({xa + cr*std::cos(t), cy + cr*std::sin(t)});
    float period = dash + gap, acc = 0;
    for (size_t i = 0; i < pts.size(); i++) {
        auto &p = pts[i], &q = pts[(i+1) % pts.size()];
        if (std::fmod(acc + phase, period) < dash)
            draw_line(img, p.first, p.second, q.first, q.second, width, r, g, b, a);
        acc += std::hypot(q.first - p.first, q.second - p.second);
    }
}
inline Img g_gif_ind_cache[2];
inline bool g_gif_ind_built[2] = {false, false};
inline Img build_gif_mode_indicator(int phase_index, TextEngine &te = g_te) {
    int k = phase_index % 2;
    const int S = 3, big = 240*S, pad = 10*S;
    Font *f = te.font(24*S);
    int b0,b1,b2,b3; te.textbbox(f, "GIF", b0,b1,b2,b3);
    float pill_h = 34*S, pill_w = (b2-b0) + 20*S;
    float x1 = big - pad, x0 = x1 - pill_w;
    float y0 = pad + pill_h + 8*S, y1 = y0 + pill_h, cr = pill_h/2;
    int tx = (int)(x0 + (pill_w - (b0+b2))/2), ty = (int)(y0 + (pill_h - (b1+b3))/2);
    float dash = 6*S, gap = 5*S, period = dash + gap;
    float phase = period * k / 2;
    Img shadow(big, big), white(big, big);
    _dashed_capsule(shadow, x0, y0, x1, y1, cr, phase, dash, gap, 3*S, 0,0,0,250);
    te.draw(shadow, f, tx, ty, "GIF", 0,0,0,250);
    _dashed_capsule(white, x0, y0, x1, y1, cr, phase, dash, gap, 2*S, 255,255,255,255);
    te.draw(white, f, tx, ty, "GIF", 255,255,255,255);
    gaussian_blur_roi(shadow, 4*S);
    alpha_composite(shadow, white);
    return resize_lanczos(shadow, 240, 240);
}
inline Img &gif_mode_indicator(int phase_index) {      /* 2 pre-rendered phases */
    int k = phase_index % 2;
    if (!g_gif_ind_built[k]) {
        g_gif_ind_cache[k] = build_gif_mode_indicator(k);
        g_gif_ind_built[k] = true;
    }
    return g_gif_ind_cache[k];
}

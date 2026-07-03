/* optocam_preview.cpp — OptoCam Buildroot Stage-1 live preview.
 * libcamera capture (imx708) -> RGB565 -> ST7789 over spidev, no Python.
 * Reuses the validated st7789 display code; adds a minimal libcamera viewfinder.
 *
 * Cross-compile with the Buildroot toolchain + libcamera pkg-config (see README).
 * libcamera's C++ API shifts between versions — if it fails to compile, paste the
 * error; the fixes are usually one-liners (FrameBuffer::Plane fd accessor, etc.).
 */
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <unistd.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <map>
#include <vector>
#include <memory>
#include <linux/spi/spidev.h>
#include <linux/gpio.h>
#include <linux/media.h>
#include <libcamera/libcamera.h>

using namespace libcamera;

/* ---------- ST7789 display (identical to st7789_test.c) ---------- */
static int spifd, rst_fd, dc_fd, bl_fd;
static int gpio_out(int chipfd, unsigned off, int val) {
    struct gpiohandle_request r; memset(&r, 0, sizeof r);
    r.lineoffsets[0] = off; r.lines = 1; r.flags = GPIOHANDLE_REQUEST_OUTPUT;
    r.default_values[0] = val; strcpy(r.consumer_label, "optocam");
    if (ioctl(chipfd, GPIO_GET_LINEHANDLE_IOCTL, &r) < 0) { perror("linehandle"); return -1; }
    return r.fd;
}
static void gpio_set(int fd, int v) { struct gpiohandle_data d; d.values[0] = v; ioctl(fd, GPIOHANDLE_SET_LINE_VALUES_IOCTL, &d); }
static void spi_write(const uint8_t *b, size_t len) {
    size_t off = 0;
    while (off < len) { size_t n = len - off; if (n > 65536) n = 65536;
        ssize_t w = write(spifd, b + off, n); if (w <= 0) { perror("spi"); return; } off += (size_t)w; }
}
static void dcmd(uint8_t c) { gpio_set(dc_fd, 0); spi_write(&c, 1); }
static void ddata(const uint8_t *d, size_t n) { gpio_set(dc_fd, 1); spi_write(d, n); }

static void display_init(void) {
    int chip = open("/dev/gpiochip0", O_RDONLY);
    rst_fd = gpio_out(chip, 27, 1); dc_fd = gpio_out(chip, 25, 0); bl_fd = gpio_out(chip, 24, 0);
    spifd = open("/dev/spidev0.0", O_RDWR);
    uint8_t mode = SPI_MODE_0, bits = 8; uint32_t sp = 100000000;
    ioctl(spifd, SPI_IOC_WR_MODE, &mode); ioctl(spifd, SPI_IOC_WR_BITS_PER_WORD, &bits);
    ioctl(spifd, SPI_IOC_WR_MAX_SPEED_HZ, &sp);
    gpio_set(rst_fd, 1); usleep(50000); gpio_set(rst_fd, 0); usleep(50000); gpio_set(rst_fd, 1); usleep(80000);
    struct { uint8_t c, n, d[16]; } in[] = {
        {0x36,1,{0x70}},{0x3A,1,{0x05}},{0xB2,5,{0x0C,0x0C,0x00,0x33,0x33}},{0xB7,1,{0x35}},{0xBB,1,{0x35}},
        {0xC0,1,{0x2C}},{0xC2,1,{0x01}},{0xC3,1,{0x13}},{0xC4,1,{0x20}},{0xC6,1,{0x0F}},{0xD0,2,{0xA4,0xA1}},
        {0xE0,14,{0xF0,0x00,0x04,0x04,0x04,0x05,0x29,0x33,0x3E,0x38,0x12,0x12,0x28,0x30}},
        {0xE1,14,{0xF0,0x07,0x0A,0x0D,0x0B,0x07,0x28,0x33,0x3E,0x36,0x14,0x14,0x29,0x32}},
        {0x21,0,{0}},{0x11,0,{0}},
    };
    for (unsigned i = 0; i < sizeof(in)/sizeof(in[0]); i++) { dcmd(in[i].c); if (in[i].n) ddata(in[i].d, in[i].n); if (in[i].c == 0x11) usleep(80000); }
    dcmd(0x29); gpio_set(bl_fd, 1);
}
static uint8_t fb565[240*240*2];
static unsigned FW, FH, FSTRIDE;
static void display_blit(const uint8_t *rgb) {
    unsigned ox = FW > 240 ? (FW - 240) / 2 : 0, oy = FH > 240 ? (FH - 240) / 2 : 0;
    for (int y = 0; y < 240; y++) {
        uint8_t *out = fb565 + y * 240 * 2;
        for (int x = 0; x < 240; x++) {
            /* rotate 90° to match the panel mounting (the Python app's ROTATE_90).
             * If it comes out 180° or mirrored, flip to: sx = y; sy = 239 - x; */
            int sx = 239 - y, sy = x;
            const uint8_t *px = rgb + (size_t)(oy + sy) * FSTRIDE + (size_t)(ox + sx) * 3;
            /* libcamera RGB888 is stored B,G,R in memory */
            uint8_t b = px[0], g = px[1], r = px[2];
            uint16_t c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
            out[x*2] = c >> 8; out[x*2+1] = c & 0xFF;
        }
    }
    uint8_t win[4] = {0x00,0x00,0x00,0xEF};
    dcmd(0x2A); ddata(win, 4); dcmd(0x2B); ddata(win, 4); dcmd(0x2C); ddata(fb565, sizeof fb565);
}

/* ---------- libcamera capture ---------- */
static std::shared_ptr<Camera> camera;
static Stream *stream;
static std::map<FrameBuffer *, void *> memmap;

static void requestComplete(Request *req) {
    static int fc = 0;
    if (req->status() == Request::RequestCancelled) return;
    if (fc++ == 0) { printf("optocam: FIRST FRAME\n"); fflush(stdout); }
    FrameBuffer *buf = req->buffers().begin()->second;
    display_blit(static_cast<uint8_t *>(memmap[buf]));
    req->reuse(Request::ReuseBuffers);
    camera->queueRequest(req);
}

int main() {
    display_init();
    /* paint black immediately so the wait shows a clean screen, not panel static */
    memset(fb565, 0, sizeof fb565);
    { uint8_t w[4] = {0x00,0x00,0x00,0xEF};
      dcmd(0x2A); ddata(w, 4); dcmd(0x2B); ddata(w, 4); dcmd(0x2C); ddata(fb565, sizeof fb565); }
    printf("optocam: display up, starting camera...\n");

    /* Warm libcamera's libraries, IPA module and tuning file into the page
     * cache while we sit idle waiting for the sensor link below. Cold SD reads
     * at cm.start() cost ~2s; prefetched they're RAM hits. Detached shell job,
     * never blocks the wait loop. */
    system("for f in /usr/lib/libcamera*.so* /usr/lib/libcamera/* "
           "/usr/libexec/libcamera/* /usr/share/libcamera/ipa/rpi/vc4/*; do "
           "cat \"$f\" >/dev/null 2>&1; done &");

    /* unicam + the sensor register asynchronously and variably (5-20s). Wait for
     * the unicam media device to actually exist BEFORE starting libcamera, so we
     * never race it and never churn the CameraManager. */
    /* Wait for the imx708 sensor to actually be LINKED into a media graph (i.e. an
     * entity named imx708 exists) — not just the unicam device. That's the true
     * "camera ready" condition, so libcamera enumerates on the first try and the
     * preview shows up consistently the instant the sensor is up. */
    printf("optocam: waiting for imx708 sensor link...\n"); fflush(stdout);
    for (int t = 0; t < 2000; t++) {         /* 20ms polls, up to 40s */
        int found = 0;
        for (int m = 0; m < 8 && !found; m++) {
            char path[32]; snprintf(path, sizeof path, "/dev/media%d", m);
            int fd = open(path, O_RDONLY);
            if (fd < 0) continue;
            struct media_entity_desc e; memset(&e, 0, sizeof e);
            while (1) {
                e.id |= MEDIA_ENT_ID_FLAG_NEXT;
                if (ioctl(fd, MEDIA_IOC_ENUM_ENTITIES, &e) < 0) break;
                if (strstr(e.name, "imx708")) { found = 1; break; }
            }
            close(fd);
        }
        if (found) { printf("optocam: imx708 linked at ~%d ms\n", t * 20); fflush(stdout); break; }
        usleep(20000);
    }

    setenv("LIBCAMERA_LOG_LEVELS", "*:ERROR", 1);  /* skip log formatting/writes */
    CameraManager cm; cm.start();
    for (int i = 0; i < 50 && cm.cameras().empty(); i++) usleep(100000);  /* brief settle */
    if (cm.cameras().empty()) { printf("optocam: NO CAMERAS\n"); return 1; }
    printf("optocam: camera found\n"); fflush(stdout);
    camera = cm.cameras()[0];
    camera->acquire();

    std::unique_ptr<CameraConfiguration> config = camera->generateConfiguration({ StreamRole::Viewfinder });
    StreamConfiguration &cfg = config->at(0);
    cfg.size = Size(240, 240);
    cfg.pixelFormat = formats::RGB888;
    cfg.bufferCount = 4;
    config->validate();
    camera->configure(config.get());
    FW = cfg.size.width; FH = cfg.size.height; FSTRIDE = cfg.stride;
    stream = cfg.stream();
    printf("optocam: stream %ux%u stride %u fmt %s\n", FW, FH, FSTRIDE, cfg.pixelFormat.toString().c_str());

    fflush(stdout);
    FrameBufferAllocator *alloc = new FrameBufferAllocator(camera);
    if (alloc->allocate(stream) < 0) { printf("optocam: allocate FAILED\n"); return 1; }
    std::vector<std::unique_ptr<Request>> requests;
    int nbuf = 0;
    for (const std::unique_ptr<FrameBuffer> &buffer : alloc->buffers(stream)) {
        const FrameBuffer::Plane &p = buffer->planes()[0];
        void *mem = mmap(nullptr, p.length, PROT_READ, MAP_SHARED, p.fd.get(), p.offset);
        if (mem == MAP_FAILED) { printf("optocam: mmap FAILED\n"); return 1; }
        memmap[buffer.get()] = mem;
        std::unique_ptr<Request> req = camera->createRequest();
        if (!req) { printf("optocam: createRequest FAILED\n"); return 1; }
        req->addBuffer(stream, buffer.get());
        requests.push_back(std::move(req));
        nbuf++;
    }
    printf("optocam: %d buffers mapped\n", nbuf); fflush(stdout);
    camera->requestCompleted.connect(requestComplete);
    if (camera->start() < 0) { printf("optocam: start FAILED\n"); return 1; }
    printf("optocam: camera started\n"); fflush(stdout);
    for (auto &r : requests) camera->queueRequest(r.get());
    printf("optocam: %zu requests queued, waiting for frames\n", requests.size()); fflush(stdout);

    pause();   /* libcamera runs the capture on its own thread */
    return 0;
}

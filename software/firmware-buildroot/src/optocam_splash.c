/* optocam_splash.c — earliest-possible splash: runs as soon as spidev exists,
 * long before the camera app. Inits the ST7789, blits splash.raw, signals the
 * app via /tmp/display-up, then pauses so the GPIO lines (backlight) stay held
 * until the app takes over (it kills us and re-grabs the lines). */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <linux/spi/spidev.h>
#include <linux/gpio.h>

static int spifd, dc_fd;
static int gpio_out(int chip, unsigned off, int val) {
    struct gpiohandle_request r; memset(&r, 0, sizeof r);
    r.lineoffsets[0] = off; r.lines = 1; r.flags = GPIOHANDLE_REQUEST_OUTPUT;
    r.default_values[0] = val; strcpy(r.consumer_label, "splash");
    if (ioctl(chip, GPIO_GET_LINEHANDLE_IOCTL, &r) < 0) return -1;
    return r.fd;
}
static void gset(int fd, int v) { struct gpiohandle_data d; d.values[0] = v; ioctl(fd, GPIOHANDLE_SET_LINE_VALUES_IOCTL, &d); }
static void spiw(const uint8_t *b, size_t n) {
    size_t o = 0; while (o < n) { size_t c = n - o > 65536 ? 65536 : n - o;
        ssize_t w = write(spifd, b + o, c); if (w <= 0) return; o += (size_t)w; }
}
static void cmd(uint8_t c) { gset(dc_fd, 0); spiw(&c, 1); }
static void dat(const uint8_t *d, size_t n) { gset(dc_fd, 1); spiw(d, n); }

int main(void) {
    int chip = -1, rst = -1, bl = -1;
    for (int t = 0; t < 250 && (chip = open("/dev/gpiochip0", O_RDONLY)) < 0; t++) usleep(20000);
    /* backlight claimed HIGH: it's been lit since power-on (hat pull-up +
     * firmware gpio=24=op,dh) as the user's "device is alive" cue — claiming
     * it low here caused a visible off-on blink before the splash */
    rst = gpio_out(chip, 27, 1); dc_fd = gpio_out(chip, 25, 0); bl = gpio_out(chip, 24, 1);
    for (int t = 0; t < 250 && (spifd = open("/dev/spidev0.0", O_RDWR)) < 0; t++) usleep(20000);
    if (spifd < 0 || rst < 0) return 1;
    uint8_t mode = SPI_MODE_0, bits = 8; uint32_t sp = 100000000;
    ioctl(spifd, SPI_IOC_WR_MODE, &mode); ioctl(spifd, SPI_IOC_WR_BITS_PER_WORD, &bits);
    ioctl(spifd, SPI_IOC_WR_MAX_SPEED_HZ, &sp);
    gset(rst, 1); usleep(50000); gset(rst, 0); usleep(50000); gset(rst, 1); usleep(80000);
    struct { uint8_t c, n, d[16]; } in[] = {
        {0x36,1,{0x00}},{0x3A,1,{0x05}},{0xB2,5,{0x0C,0x0C,0x00,0x33,0x33}},{0xB7,1,{0x35}},{0xBB,1,{0x35}},
        {0xC0,1,{0x2C}},{0xC2,1,{0x01}},{0xC3,1,{0x13}},{0xC4,1,{0x20}},{0xC6,1,{0x0F}},{0xD0,2,{0xA4,0xA1}},
        {0xE0,14,{0xF0,0x00,0x04,0x04,0x04,0x05,0x29,0x33,0x3E,0x38,0x12,0x12,0x28,0x30}},
        {0xE1,14,{0xF0,0x07,0x0A,0x0D,0x0B,0x07,0x28,0x33,0x3E,0x36,0x14,0x14,0x29,0x32}},
        {0x21,0,{0}},{0x11,0,{0}},
    };
    for (unsigned i = 0; i < sizeof(in)/sizeof(in[0]); i++) {
        cmd(in[i].c); if (in[i].n) dat(in[i].d, in[i].n); if (in[i].c == 0x11) usleep(80000);
    }
    /* NOTE: display-ON (0x29) deliberately NOT sent yet — the panel RAM still
     * holds power-on garbage; with the backlight pin floating high through
     * the firmware phase that garbage was visible as a static flash */
    static uint8_t vis[240*240*2], fb[240*240*2];
    FILE *f = fopen("/usr/share/optocam/splash.raw", "rb");
    if (f) { size_t rd = fread(vis, 1, sizeof vis, f); (void)rd; fclose(f); }
    /* calibrated variant-2 transform (matches the app): fb[i][j] = vis[i, 239-j] */
    for (int i = 0; i < 240; i++)
        for (int j = 0; j < 240; j++) {
            fb[(i*240 + j)*2]     = vis[((239 - j)*240 + i)*2];
            fb[(i*240 + j)*2 + 1] = vis[((239 - j)*240 + i)*2 + 1];
        }
    uint8_t win[4] = {0x00,0x00,0x00,0xEF};
    cmd(0x2A); dat(win, 4); cmd(0x2B); dat(win, 4); cmd(0x2C); dat(fb, sizeof fb);
    cmd(0x29);                     /* RAM now holds the splash — display on */
    gset(bl, 1);
    FILE *flag = fopen("/tmp/display-up", "w");
    if (flag) fclose(flag);
    pause();                       /* hold the GPIO lines until the app kills us */
    return 0;
}

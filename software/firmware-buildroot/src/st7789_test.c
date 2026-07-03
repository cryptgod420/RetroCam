/* st7789_test.c — OptoCam Buildroot Stage-1 display test.
 * Drives the ST7789 240x240 panel directly over /dev/spidev0.0 + gpiochip ioctls,
 * mirroring the Python init_display()/display_image() exactly. Shows vertical
 * colour bars (RGB565, big-endian) to prove the panel + byte order, then holds.
 *
 * Wiring (from the app): SPI0 CS0, mode 0, 100 MHz; DC=GPIO25, RST=GPIO27, BL=GPIO24.
 * Cross-compile with the Buildroot toolchain (see README). No external libs.
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <linux/spi/spidev.h>
#include <linux/gpio.h>

#define W 240
#define H 240

static int spifd;
static int rst_fd, dc_fd, bl_fd;

static int gpio_out(int chipfd, unsigned off, int val) {
    struct gpiohandle_request r;
    memset(&r, 0, sizeof r);
    r.lineoffsets[0] = off;
    r.lines = 1;
    r.flags = GPIOHANDLE_REQUEST_OUTPUT;
    r.default_values[0] = val;
    strcpy(r.consumer_label, "st7789");
    if (ioctl(chipfd, GPIO_GET_LINEHANDLE_IOCTL, &r) < 0) { perror("linehandle"); return -1; }
    return r.fd;
}
static void gpio_set(int fd, int val) {
    struct gpiohandle_data d;
    d.values[0] = val;
    ioctl(fd, GPIOHANDLE_SET_LINE_VALUES_IOCTL, &d);
}
static void spi_write(const uint8_t *buf, size_t len) {
    size_t off = 0;
    while (off < len) {
        size_t n = len - off; if (n > 65536) n = 65536;
        ssize_t w = write(spifd, buf + off, n);
        if (w <= 0) { perror("spi write"); return; }
        off += (size_t)w;
    }
}
static void cmd(uint8_t c)                    { gpio_set(dc_fd, 0); spi_write(&c, 1); }
static void data(const uint8_t *d, size_t n)  { gpio_set(dc_fd, 1); spi_write(d, n); }

int main(void) {
    int chipfd = open("/dev/gpiochip0", O_RDONLY);
    if (chipfd < 0) { perror("gpiochip0"); return 1; }
    rst_fd = gpio_out(chipfd, 27, 1);
    dc_fd  = gpio_out(chipfd, 25, 0);
    bl_fd  = gpio_out(chipfd, 24, 0);   /* backlight off until frame drawn */
    if (rst_fd < 0 || dc_fd < 0 || bl_fd < 0) return 1;

    spifd = open("/dev/spidev0.0", O_RDWR);
    if (spifd < 0) { perror("spidev0.0"); return 1; }
    uint8_t mode = SPI_MODE_0, bits = 8; uint32_t speed = 100000000;
    ioctl(spifd, SPI_IOC_WR_MODE, &mode);
    ioctl(spifd, SPI_IOC_WR_BITS_PER_WORD, &bits);
    ioctl(spifd, SPI_IOC_WR_MAX_SPEED_HZ, &speed);

    /* Hardware reset */
    gpio_set(rst_fd, 1); usleep(50000);
    gpio_set(rst_fd, 0); usleep(50000);
    gpio_set(rst_fd, 1); usleep(80000);

    /* Init sequence — identical to the Python init_display() */
    struct { uint8_t c, n, d[16]; } init[] = {
        {0x36,1,{0x70}}, {0x3A,1,{0x05}},
        {0xB2,5,{0x0C,0x0C,0x00,0x33,0x33}}, {0xB7,1,{0x35}}, {0xBB,1,{0x35}},
        {0xC0,1,{0x2C}}, {0xC2,1,{0x01}}, {0xC3,1,{0x13}}, {0xC4,1,{0x20}}, {0xC6,1,{0x0F}},
        {0xD0,2,{0xA4,0xA1}},
        {0xE0,14,{0xF0,0x00,0x04,0x04,0x04,0x05,0x29,0x33,0x3E,0x38,0x12,0x12,0x28,0x30}},
        {0xE1,14,{0xF0,0x07,0x0A,0x0D,0x0B,0x07,0x28,0x33,0x3E,0x36,0x14,0x14,0x29,0x32}},
        {0x21,0,{0}}, {0x11,0,{0}},
    };
    for (unsigned i = 0; i < sizeof(init)/sizeof(init[0]); i++) {
        cmd(init[i].c);
        if (init[i].n) data(init[i].d, init[i].n);
        if (init[i].c == 0x11) usleep(80000);
    }
    cmd(0x29); /* display ON */

    /* Colour bars, RGB565 big-endian */
    static uint8_t fb[W*H*2];
    uint16_t bars[8] = {0xFFFF,0x0000,0xF800,0x07E0,0x001F,0xFFE0,0x07FF,0xF81F};
    for (int y = 0; y < H; y++)
        for (int x = 0; x < W; x++) {
            uint16_t c = bars[x / (W/8)];
            int i = (y*W + x) * 2;
            fb[i] = c >> 8; fb[i+1] = c & 0xFF;
        }

    uint8_t win[4] = {0x00,0x00,0x00,0xEF};   /* 0..239 */
    cmd(0x2A); data(win, 4);
    cmd(0x2B); data(win, 4);
    cmd(0x2C); data(fb, sizeof fb);

    gpio_set(bl_fd, 1);   /* backlight on */
    printf("st7789_test: colour bars shown\n");

    /* hold the process so the GPIO handles (backlight) stay asserted */
    pause();
    return 0;
}

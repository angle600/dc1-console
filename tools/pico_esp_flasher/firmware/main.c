/*
 * pico_esp_flasher - RP2040 (Pico) 多路串口 + ESP 下载器
 *
 * USB 复合设备 3 路 CDC
 *   CDC0 (ITF0) -> UART0 (GP0 TX / GP1 RX)  ESP 数据口，受 3.3V 电源控制
 *   CDC1 (ITF1) -> UART1 (GP8 TX / GP9 RX)  第二路直通，常开
 *   CDC2 (ITF2) -> 控制台，ASCII 命令
 *
 * 极性不写死：
 *   GP 命令直接操作除 GP0/GP1/GP8/GP9 外的任意 GPIO
 *   PIN / POL 可在运行时改 PWR / IO0 / EN 的引脚号与有效电平
 *
 * 默认映射（DC1/TC1 模组实测）
 *   PWR -> GP4, 低电平 = 上电（NMOS 接 3V3_EN 反相）
 *   IO0 -> GP2, 低电平 = 拉低（直连）
 *   EN  -> GP3, 高电平 = 拉低（模组无此线，保留）
 */

#include <ctype.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pico/stdlib.h"
#include "pico/time.h"
#include "pico/bootrom.h"
#include "hardware/gpio.h"
#include "hardware/uart.h"
#include "tusb.h"

#ifdef WITH_DAP
#include "cmsis_dap.h"
#endif

/* ---------------- 基础配置 ---------------- */
#define PIN_LED       25

#define ITF_DATA0     0
#define ITF_DATA1     1
#define ITF_CTRL      2

#define FW_VERSION    "1.4.0"

#define UART_DEFAULT_BAUD 115200u
#define UART_MAX_BAUD     3000000u

#define CHAN_COUNT    2
#define RING_SZ       4096

/* 可被 GP 命令操作的引脚上限；23/24/29 是芯片内部功能 */
#define GPIO_MAX      28

/* ---------------- 通道 ---------------- */

typedef struct {
    uart_inst_t *uart;
    uint8_t      pin_tx;
    uint8_t      pin_rx;
    uint32_t     baud;
    uint32_t     rx_bytes;
    uint32_t     tx_bytes;
    uint8_t      ring[RING_SZ];
    uint32_t     head;
    uint32_t     tail;
} chan_t;

static chan_t chan[CHAN_COUNT];

typedef struct {
    bool     pwr;
    bool     io0_low;
    bool     en_low;
    bool     auto_rst;
    uint8_t  pin_pwr;
    uint8_t  pin_io0;
    uint8_t  pin_en;
    bool     pol_pwr;
    bool     pol_io0;
    bool     pol_en;
} ctx_t;

/* 默认按 DC1/TC1 实测接线；AUTO 关闭（模组无 EN 线） */
static ctx_t g = {
    .pwr = false, .io0_low = false, .en_low = false, .auto_rst = false,
    .pin_pwr = 4, .pin_io0 = 2, .pin_en = 3,
    .pol_pwr = false, .pol_io0 = false, .pol_en = true,
};

static volatile uint32_t g_line_state = 0;
static volatile bool     g_line_dirty = false;

/* ---------------- 基础 ---------------- */

static inline void led(bool on) { gpio_put(PIN_LED, on ? 1 : 0); }

static inline void drive(uint8_t pin, bool active, bool pol)
{
    gpio_put(pin, (active == pol) ? 1 : 0);
}

static bool pin_ok(int pin)
{
    if (pin < 0 || pin > GPIO_MAX) return false;
    if (pin == 0 || pin == 1) return false;          /* UART0 */
    if (pin == 8 || pin == 9) return false;          /* UART1 */
#ifdef WITH_DAP
    if (pin == 13 || pin == 14 || pin == 15) return false;  /* SWD */
#endif
    if (pin == 23 || pin == 24 || pin == 29) return false;
    return true;
}

/* 需要驱动时才把引脚切成输出；默认全部是高阻输入，不干扰外部电路 */
static inline void claim(uint8_t pin)
{
    gpio_init(pin);
    gpio_set_dir(pin, GPIO_OUT);
}

static void esp_io0(bool low)
{
    claim(g.pin_io0);
    drive(g.pin_io0, low, g.pol_io0);
    g.io0_low = low;
}

static void esp_en(bool low)
{
    claim(g.pin_en);
    drive(g.pin_en, low, g.pol_en);
    g.en_low = low;
}

static void esp_pwr(bool on)
{
    claim(g.pin_pwr);
    if (on) {
        gpio_set_function(chan[0].pin_tx, GPIO_FUNC_UART);
        drive(g.pin_pwr, true, g.pol_pwr);
        g.pwr = true;
        sleep_ms(25);
    } else {
        /* 断电前把 TX 拉低并转输入，切断经 ESP RX 的 ESD 二极管倒灌 */
        gpio_set_function(chan[0].pin_tx, GPIO_FUNC_SIO);
        gpio_set_dir(chan[0].pin_tx, GPIO_OUT);
        gpio_put(chan[0].pin_tx, 0);
        gpio_set_dir(chan[0].pin_tx, GPIO_IN);
        drive(g.pin_pwr, false, g.pol_pwr);
        g.pwr = false;
        sleep_ms(10);
    }
}

/* 把三个控制脚全部释放成高阻输入，Pico 对外部电路完全透明 */
static void release_all(void)
{
    uint8_t pins[3] = { g.pin_pwr, g.pin_io0, g.pin_en };
    for (int i = 0; i < 3; i++) {
        gpio_init(pins[i]);
        gpio_set_dir(pins[i], GPIO_IN);
        gpio_disable_pulls(pins[i]);
    }
    g.pwr = false;
    g.io0_low = false;
    g.en_low = false;
}

/* 只释放某一路（RUN 之后用：IO0/EN 放开，PWR 继续供电） */
static void release_pin(uint8_t pin)
{
    gpio_init(pin);
    gpio_set_dir(pin, GPIO_IN);
    gpio_disable_pulls(pin);
}

/*
 * DC1/TC1 模组没有 CH_PD/EN 线，只能靠上电复位采样 IO0：
 *   烧录：断电 0.5s -> IO0 有效 -> 上电 0.5s
 *   运行：断电 0.5s -> IO0 释放 -> 上电 0.5s
 * 有 EN 线的模组用同一套也能进 bootloader。
 */
static void esp_boot_download(void)
{
    esp_pwr(false);
    sleep_ms(500);
    esp_io0(true);
    sleep_ms(20);
    esp_pwr(true);
    sleep_ms(500);
}

static void esp_reset_run(void)
{
    esp_pwr(false);
    sleep_ms(500);
    esp_io0(false);
    sleep_ms(20);
    esp_pwr(true);
    sleep_ms(500);
}

/* ---------------- 通道数据泵 ---------------- */

static inline uint32_t chan_used(const chan_t *c)
{
    return (c->head + RING_SZ - c->tail) % RING_SZ;
}

static void drain_cdc(uint8_t itf)
{
    while (tud_cdc_n_available(itf)) {
        uint8_t tmp[64];
        if (tud_cdc_n_read(itf, tmp, sizeof tmp) == 0) break;
    }
}

static void pump_cdc_to_uart(chan_t *c, uint8_t itf)
{
    /* CDC -> ring，不阻塞 */
    while (tud_cdc_n_available(itf)) {
        uint32_t free_space = RING_SZ - 1u - chan_used(c);
        if (free_space == 0) break;

        uint8_t buf[64];
        uint32_t want = free_space < sizeof buf ? free_space : (uint32_t)sizeof buf;
        uint32_t n = tud_cdc_n_read(itf, buf, want);
        if (n == 0) break;

        for (uint32_t i = 0; i < n; i++) {
            c->ring[c->head] = buf[i];
            c->head = (c->head + 1u) % RING_SZ;
        }
        c->tx_bytes += n;
    }

    /* ring -> UART，每批最多 32 字节（115200 下约 2.8ms） */
    while (c->tail != c->head) {
        uint32_t cnt = chan_used(c);
        if (cnt > 32u) cnt = 32u;
        if (c->tail + cnt <= RING_SZ) {
            uart_write_blocking(c->uart, &c->ring[c->tail], (size_t)cnt);
            c->tail = (c->tail + cnt) % RING_SZ;
        } else {
            uint32_t first = RING_SZ - c->tail;
            uart_write_blocking(c->uart, &c->ring[c->tail], (size_t)first);
            c->tail = 0;
        }
    }
}

static void pump_uart_to_cdc(chan_t *c, uint8_t itf)
{
    while (uart_is_readable(c->uart)) {
        uint32_t avail = tud_cdc_n_write_available(itf);
        if (avail == 0) {
            tud_cdc_n_write_flush(itf);
            avail = tud_cdc_n_write_available(itf);
            if (avail == 0) break;
        }

        uint8_t buf[64];
        uint32_t max = avail < sizeof buf ? avail : (uint32_t)sizeof buf;
        uint32_t n = 0;
        while (n < max && uart_is_readable(c->uart)) {
            buf[n++] = (uint8_t)uart_getc(c->uart);
        }
        if (n == 0) break;

        c->rx_bytes += n;
        tud_cdc_n_write(itf, buf, n);
    }
    tud_cdc_n_write_flush(itf);
}

static void pump_data(void)
{
    /* UART0：受 3.3V 电源控制（ESP 那一路） */
    if (g.pwr) {
        pump_cdc_to_uart(&chan[0], ITF_DATA0);
        pump_uart_to_cdc(&chan[0], ITF_DATA0);
    } else {
        drain_cdc(ITF_DATA0);
        chan[0].head = chan[0].tail = 0;
    }

    /* UART1：常开 */
    pump_cdc_to_uart(&chan[1], ITF_DATA1);
    pump_uart_to_cdc(&chan[1], ITF_DATA1);
}

#ifdef WITH_DAP
/*
 * CMSIS-DAP 事务。单帧最长 64 字节，内部可能做 60+ 次 AP 访问（每次 ~10us），
 * 期间不处理 CDC —— 数据已经在 ring 里，事后照常转发。
 */
static void pump_dap(void)
{
    static uint8_t ibuf[64];
    static uint8_t obuf[64];

    if (!tud_vendor_available()) return;

    uint32_t n = tud_vendor_read(ibuf, sizeof ibuf);
    if (n == 0) return;

    uint32_t r = dap_process(ibuf, n, obuf, sizeof obuf);
    if (r > 0) {
        tud_vendor_write(obuf, r);
        tud_vendor_write_flush();
    }
}
#endif

/* ---------------- CDC 回调 ---------------- */

void tud_cdc_line_state_cb(uint8_t itf, bool dtr, bool rts)
{
    if (itf != ITF_DATA0) return;
    g_line_state = (dtr ? 1u : 0u) | (rts ? 2u : 0u);
    g_line_dirty = true;
}

void tud_cdc_line_coding_cb(uint8_t itf, const cdc_line_coding_t *lc)
{
    if (lc == NULL || itf >= CHAN_COUNT) return;
    uint32_t br = lc->bit_rate;
    if (br < 300u) return;
    if (br > UART_MAX_BAUD) br = UART_MAX_BAUD;

    chan_t *c = &chan[itf];
    if (br != c->baud) {
        c->baud = br;
        uart_set_baudrate(c->uart, br);
    }
}

/* ---------------- 控制台 ---------------- */

static void ctl_printf(const char *fmt, ...)
{
    char buf[224];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    if (n <= 0) return;
    if (n > (int)sizeof buf - 1) n = (int)sizeof buf - 1;

    uint32_t dl = time_us_32() + 200000u;
    while (tud_cdc_n_connected(ITF_CTRL) &&
           tud_cdc_n_write_available(ITF_CTRL) < (uint32_t)n &&
           (int32_t)(dl - time_us_32()) > 0) {
        tud_task();
    }
    tud_cdc_n_write(ITF_CTRL, buf, (uint32_t)n);
    tud_cdc_n_write_flush(ITF_CTRL);
}

static void ctl_status(void)
{
    ctl_printf("STATUS pwr=%d io0=%d en=%d auto=%d\r\n",
               g.pwr ? 1 : 0, g.io0_low ? 1 : 0, g.en_low ? 1 : 0,
               g.auto_rst ? 1 : 0);
    for (int i = 0; i < CHAN_COUNT; i++) {
        ctl_printf("CH%d GP%u/GP%u baud=%lu rx=%lu tx=%lu\r\n", i,
                   chan[i].pin_tx, chan[i].pin_rx,
                   (unsigned long)chan[i].baud,
                   (unsigned long)chan[i].rx_bytes,
                   (unsigned long)chan[i].tx_bytes);
    }
    ctl_printf("MAP pwr=GP%u/%c io0=GP%u/%c en=GP%u/%c\r\n",
               g.pin_pwr, g.pol_pwr ? 'H' : 'L',
               g.pin_io0, g.pol_io0 ? 'H' : 'L',
               g.pin_en,  g.pol_en  ? 'H' : 'L');
    ctl_printf("DIR pwr=%s io0=%s en=%s\r\n",
               gpio_get_dir(g.pin_pwr) ? "OUT" : "IN",
               gpio_get_dir(g.pin_io0) ? "OUT" : "IN",
               gpio_get_dir(g.pin_en)  ? "OUT" : "IN");
}

static void ctl_help(void)
{
    ctl_printf("PICO-ESP-FLASHER %s\r\n", FW_VERSION);
    ctl_printf("-- raw gpio --\r\n");
    ctl_printf("GP <n> <0|1>      any gpio except UART / SWD / internal\r\n");
    ctl_printf("GPIN <n> / GPREAD <n>\r\n");
    ctl_printf("TX <hex...>       write bytes straight to UART0\r\n");
    ctl_printf("LOOPBACK 0|1      UART0 internal loopback\r\n");
    ctl_printf("-- uart --\r\n");
    ctl_printf("UART0 = GP0/GP1   UART1 = GP8/GP9\r\n");
    ctl_printf("UART <0|1> <baud> set one channel baudrate\r\n");
    ctl_printf("BAUD <n>          set both channels\r\n");
#ifdef WITH_DAP
    ctl_printf("-- swd / dap --\r\n");
    ctl_printf("GP14=SWCLK GP15=SWDIO GP13=nRESET (CMSIS-DAP v2)\r\n");
#endif
    ctl_printf("-- semantic --\r\n");
    ctl_printf("PWR 0|1|CYCLE     power rail (UART0 side)\r\n");
    ctl_printf("IO0 0|1 / EN 0|1\r\n");
    ctl_printf("BOOT / RUN        power-cycle timing\r\n");
    ctl_printf("-- mapping --\r\n");
    ctl_printf("PIN pwr|io0|en <n>\r\n");
    ctl_printf("POL pwr|io0|en <0|1>  1=active high 0=active low\r\n");
    ctl_printf("RELEASE           all control pins -> Hi-Z input\r\n");
    ctl_printf("AUTO 0|1          DTR/RTS -> IO0/EN (UART0 side)\r\n");
    ctl_printf("BOOTSEL | STATUS | PINS | HELP\r\n");
}

static int parse_bool(const char *s, bool *out)
{
    if (!s) return -1;
    if (!strcmp(s, "1") || !strcmp(s, "ON") || !strcmp(s, "HIGH")) { *out = true;  return 0; }
    if (!strcmp(s, "0") || !strcmp(s, "OFF") || !strcmp(s, "LOW")) { *out = false; return 0; }
    return -1;
}

static int map_slot(const char *name)
{
    if (!strcmp(name, "PWR")) return 0;
    if (!strcmp(name, "IO0")) return 1;
    if (!strcmp(name, "EN"))  return 2;
    return -1;
}

static void exec_line(char *s)
{
    char *arg = strchr(s, ' ');
    if (arg) { *arg++ = '\0'; while (*arg == ' ') arg++; }
    for (char *p = s; *p; p++) *p = (char)toupper((unsigned char)*p);
    if (*s == '\0') return;

    /* ---------- 裸 GPIO ---------- */
    if (!strcmp(s, "GP")) {
        int pin = -1, val = -1;
        if (arg && sscanf(arg, "%d %d", &pin, &val) == 2) {
            if (!pin_ok(pin)) { ctl_printf("ERR GP pin %d invalid\r\n", pin); return; }
            if (val != 0 && val != 1) { ctl_printf("ERR GP needs 0|1\r\n"); return; }
            gpio_init((uint)pin);
            gpio_set_dir((uint)pin, GPIO_OUT);
            gpio_put((uint)pin, val);
            ctl_printf("OK GP %d %d\r\n", pin, val);
        } else {
            ctl_printf("ERR GP <n> <0|1>\r\n");
        }
        return;
    }
    if (!strcmp(s, "GPIN")) {
        int pin = arg ? atoi(arg) : -1;
        if (!pin_ok(pin)) { ctl_printf("ERR GPIN pin invalid\r\n"); return; }
        gpio_init((uint)pin);
        gpio_set_dir((uint)pin, GPIO_IN);
        ctl_printf("OK GPIN %d\r\n", pin);
        return;
    }
    if (!strcmp(s, "GPREAD")) {
        int pin = arg ? atoi(arg) : -1;
        if (!pin_ok(pin)) { ctl_printf("ERR GPREAD pin invalid\r\n"); return; }
        ctl_printf("OK GPREAD %d %d\r\n", pin, gpio_get((uint)pin) ? 1 : 0);
        return;
    }
    if (!strcmp(s, "TX")) {
        int n = 0;
        uint8_t buf[64];
        const char *p = arg;
        while (p && *p && n < (int)sizeof buf) {
            while (*p == ' ' || *p == ',') p++;
            if (!*p) break;
            char *end;
            long v = strtol(p, &end, 16);
            if (end == p) break;
            buf[n++] = (uint8_t)(v & 0xFF);
            p = end;
        }
        if (n > 0) {
            uart_write_blocking(chan[0].uart, buf, (size_t)n);
            ctl_printf("OK TX %d\r\n", n);
        } else {
            ctl_printf("ERR TX <hex bytes>\r\n");
        }
        return;
    }
    if (!strcmp(s, "LOOPBACK")) {
        bool on;
        if (parse_bool(arg, &on) == 0) {
            if (on) hw_set_bits(&uart_get_hw(chan[0].uart)->cr, UART_UARTCR_LBE_BITS);
            else    hw_clear_bits(&uart_get_hw(chan[0].uart)->cr, UART_UARTCR_LBE_BITS);
            ctl_printf("OK LOOPBACK %d\r\n", on ? 1 : 0);
        } else {
            ctl_printf("ERR LOOPBACK 0|1\r\n");
        }
        return;
    }

    /* ---------- UART 通道 ---------- */
    if (!strcmp(s, "UART")) {
        int n = -1;
        long b = 0;
        if (arg && sscanf(arg, "%d %ld", &n, &b) == 2 &&
            n >= 0 && n < CHAN_COUNT && b >= 300 && b <= (long)UART_MAX_BAUD) {
            chan[n].baud = (uint32_t)b;
            uart_set_baudrate(chan[n].uart, (uint32_t)b);
            ctl_printf("OK UART %d %ld\r\n", n, b);
        } else {
            ctl_printf("ERR UART <0|1> <baud>\r\n");
        }
        return;
    }

    /* ---------- 映射 ---------- */
    if (!strcmp(s, "PIN")) {
        char name[8] = {0};
        int pin = -1;
        if (arg && sscanf(arg, "%7s %d", name, &pin) == 2) {
            for (char *q = name; *q; q++) *q = (char)toupper((unsigned char)*q);
            int slot = map_slot(name);
            if (slot < 0) { ctl_printf("ERR PIN pwr|io0|en <n>\r\n"); return; }
            if (!pin_ok(pin)) { ctl_printf("ERR pin %d invalid\r\n", pin); return; }
            uint8_t old = (slot == 0) ? g.pin_pwr : (slot == 1) ? g.pin_io0 : g.pin_en;
            if (slot == 0) g.pin_pwr = (uint8_t)pin;
            if (slot == 1) g.pin_io0 = (uint8_t)pin;
            if (slot == 2) g.pin_en  = (uint8_t)pin;
            if (old != (uint8_t)pin) release_pin(old);   /* 旧脚交还高阻 */
            release_all();
            ctl_printf("OK PIN %s %d\r\n", name, pin);
        } else {
            ctl_printf("ERR PIN pwr|io0|en <n>\r\n");
        }
        return;
    }
    if (!strcmp(s, "POL")) {
        char name[8] = {0};
        int v = -1;
        if (arg && sscanf(arg, "%7s %d", name, &v) == 2 && (v == 0 || v == 1)) {
            for (char *q = name; *q; q++) *q = (char)toupper((unsigned char)*q);
            int slot = map_slot(name);
            if (slot < 0) { ctl_printf("ERR POL pwr|io0|en <0|1>\r\n"); return; }
            if (slot == 0) g.pol_pwr = (v == 1);
            if (slot == 1) g.pol_io0 = (v == 1);
            if (slot == 2) g.pol_en  = (v == 1);
            release_all();
            ctl_printf("OK POL %s %d\r\n", name, v);
        } else {
            ctl_printf("ERR POL pwr|io0|en <0|1>\r\n");
        }
        return;
    }
    if (!strcmp(s, "RELEASE") || !strcmp(s, "HIZ")) {
        /* Pico 停用：三个控制脚全部交还高阻，对外部电路完全透明 */
        release_all();
        ctl_printf("OK RELEASE all control pins -> input\r\n");
        return;
    }
    if (!strcmp(s, "PINS")) {
        ctl_status();
        return;
    }

    /* ---------- 语义 ---------- */
    if (!strcmp(s, "PWR")) {
        if (arg && !strcmp(arg, "CYCLE")) {
            esp_pwr(false);
            sleep_ms(300);
            esp_pwr(true);
            ctl_printf("OK PWR CYCLE\r\n");
        } else {
            bool on;
            if (parse_bool(arg, &on) == 0) { esp_pwr(on); ctl_printf("OK PWR %d\r\n", on); }
            else ctl_printf("ERR PWR needs 0|1|CYCLE\r\n");
        }
    } else if (!strcmp(s, "IO0")) {
        bool low;
        if (parse_bool(arg, &low) == 0) { esp_io0(low); ctl_printf("OK IO0 %d\r\n", low); }
        else ctl_printf("ERR IO0 needs 0|1\r\n");
    } else if (!strcmp(s, "EN")) {
        bool low;
        if (parse_bool(arg, &low) == 0) { esp_en(low); ctl_printf("OK EN %d\r\n", low); }
        else ctl_printf("ERR EN needs 0|1\r\n");
    } else if (!strcmp(s, "BOOT")) {
        esp_boot_download();
        ctl_printf("OK BOOT pwr=%d io0=%d en=%d\r\n", g.pwr, g.io0_low, g.en_low);
    } else if (!strcmp(s, "RUN") || !strcmp(s, "RESET")) {
        esp_reset_run();
        /* 复位完成即交还 IO0/EN，PWR 继续供电让目标运行 */
        release_pin(g.pin_io0);
        release_pin(g.pin_en);
        g.io0_low = false;
        g.en_low = false;
        ctl_printf("OK RUN (io0/en -> input, pwr held)\r\n");
    } else if (!strcmp(s, "AUTO")) {
        bool on;
        if (parse_bool(arg, &on) == 0) { g.auto_rst = on; ctl_printf("OK AUTO %d\r\n", on); }
        else ctl_printf("ERR AUTO needs 0|1\r\n");
    } else if (!strcmp(s, "BAUD")) {
        long v = arg ? strtol(arg, NULL, 10) : 0;
        if (v >= 300 && v <= (long)UART_MAX_BAUD) {
            for (int i = 0; i < CHAN_COUNT; i++) {
                chan[i].baud = (uint32_t)v;
                uart_set_baudrate(chan[i].uart, (uint32_t)v);
            }
            ctl_printf("OK BAUD %ld (both)\r\n", v);
        } else {
            ctl_printf("ERR BAUD range 300..%u\r\n", UART_MAX_BAUD);
        }
    } else if (!strcmp(s, "STATUS")) {
        ctl_status();
    } else if (!strcmp(s, "BOOTSEL") || !strcmp(s, "UF2")) {
        ctl_printf("OK BOOTSEL rebooting to RPI-RP2\r\n");
        sleep_ms(120);
        reset_usb_boot(0, 0);
    } else if (!strcmp(s, "HELP") || !strcmp(s, "?")) {
        ctl_help();
    } else if (!strcmp(s, "INFO")) {
        ctl_printf("INFO pico-esp-flasher %s build=%s %s\r\n", FW_VERSION, __DATE__, __TIME__);
    } else {
        ctl_printf("ERR unknown '%s' (HELP)\r\n", s);
    }
}

/* ---------------- 主循环任务 ---------------- */

static void handle_line_state(void)
{
    static uint32_t prev_ls  = 0xFFFFFFFFu;
    static uint32_t t_change = 0;
    static bool     suppress = false;

    uint32_t ls = g_line_state;

    if (ls != prev_ls) {
        prev_ls  = ls;
        t_change = to_ms_since_boot(get_absolute_time());
        if (ls != 0x03u) suppress = false;

        if (g.auto_rst && !suppress) {
            /* Windows 上 setRTS 走 EscapeCommFunction，不会立刻上报，
               所以只认 DTR+RTS 组合，看到就自己走完整时序 */
            if (ls == 0x03u) {
                esp_boot_download();
            } else {
                esp_io0((ls & 0x01u) != 0);
                esp_en ((ls & 0x02u) != 0);
            }
        }
        g_line_dirty = false;
    }

    if (g.auto_rst && !suppress && ls == 0x03u &&
        to_ms_since_boot(get_absolute_time()) - t_change > 2500u) {
        esp_io0(false);
        esp_en(false);
        suppress = true;
    }
}

static void pump_ctrl(void)
{
    static char line[96];
    static int  li = 0;

    while (tud_cdc_n_available(ITF_CTRL)) {
        char c;
        if (tud_cdc_n_read(ITF_CTRL, &c, 1) != 1) break;
        if (c == '\r' || c == '\n') {
            if (li > 0) { line[li] = '\0'; exec_line(line); li = 0; }
        } else if (c == 0x08 || c == 0x7f) {
            if (li > 0) li--;
        } else if (li < (int)sizeof line - 1) {
            line[li++] = c;
        }
    }
}

static void led_task(void)
{
    static uint32_t last_bytes = 0, t_act = 0;
    uint32_t now   = to_ms_since_boot(get_absolute_time());
    uint32_t bytes = chan[0].rx_bytes + chan[0].tx_bytes +
                     chan[1].rx_bytes + chan[1].tx_bytes;

    if (bytes != last_bytes) { last_bytes = bytes; t_act = now; }

    if (!g.pwr) {
        led(((now / 800u) & 1u) != 0u);
    } else if (now - t_act < 80u) {
        led(((now / 15u) & 1u) != 0u);
    } else {
        led(true);
    }
}

static void chan_init(void)
{
    chan[0].uart = uart0; chan[0].pin_tx = 0; chan[0].pin_rx = 1;
    chan[1].uart = uart1; chan[1].pin_tx = 8; chan[1].pin_rx = 9;

    for (int i = 0; i < CHAN_COUNT; i++) {
        chan[i].baud = UART_DEFAULT_BAUD;
        chan[i].head = chan[i].tail = 0;
        uart_init(chan[i].uart, chan[i].baud);
        gpio_set_function(chan[i].pin_tx, GPIO_FUNC_UART);
        gpio_set_function(chan[i].pin_rx, GPIO_FUNC_UART);
        uart_set_format(chan[i].uart, 8, 1, UART_PARITY_NONE);
        uart_set_hw_flow(chan[i].uart, false, false);
        uart_set_fifo_enabled(chan[i].uart, true);
    }
}

static void io_setup(void)
{
    /* 先把所有可用 GPIO 置为输入，避免上电瞬间误驱动外设 */
    for (int p = 0; p <= GPIO_MAX; p++) {
        if (p == 23 || p == 24 || p == 29) continue;
        gpio_init((uint)p);
        gpio_set_dir((uint)p, GPIO_IN);
        gpio_disable_pulls((uint)p);
    }

    gpio_init(PIN_LED);
    gpio_set_dir(PIN_LED, GPIO_OUT);
    gpio_put(PIN_LED, 0);

    /* 三个控制脚也保持高阻，Pico 上电不干扰外部电路 */
    release_all();
    chan_init();
#ifdef WITH_DAP
    swd_init_pins();     /* GP13 nRESET / GP14 SWCLK / GP15 SWDIO */
#endif
}

int main(void)
{
    io_setup();
    tusb_init();

    bool greeted = false;

    for (;;) {
        tud_task();
        handle_line_state();
        pump_data();
#ifdef WITH_DAP
        pump_dap();
#endif
        pump_ctrl();
        led_task();

        if (!greeted && tud_cdc_n_connected(ITF_CTRL)) {
            greeted = true;
            ctl_printf("\r\nPICO-ESP-FLASHER %s ready\r\n", FW_VERSION);
            ctl_printf("UART0=GP0/GP1  UART1=GP8/GP9  HELP for commands\r\n");
        } else if (greeted && !tud_cdc_n_connected(ITF_CTRL)) {
            greeted = false;
        }
    }

    return 0;
}

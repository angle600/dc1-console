/*
 * cmsis_dap.c - CMSIS-DAP v2 over SWD（CPU 位翻转实现）
 *
 * 为什么不用 PIO：DAP 事务在主循环里串行执行，期间 CDC 数据由环形缓冲兜住，
 * 不会被抢占；CPU 位翻转的时序抖动在 SWD 允许范围内（主机提供时钟）。
 * 若将来要同时跑高速 SWD + 高速串口，再换 PIO 版本。
 */

#include "cmsis_dap.h"

#include <string.h>

#include "pico/stdlib.h"
#include "pico/time.h"
#include "hardware/gpio.h"

/* ---------------- 物理层 ---------------- */

#define ACK_OK     1u
#define ACK_WAIT   2u
#define ACK_FAULT  4u
#define ACK_PARITY 0xFFu

/* 半周期（CPU 周期数）。125MHz / (2*1000kHz) = 62 */
static uint32_t s_half_cycles = 62;

static inline void dly(void) { busy_wait_at_least_cycles(s_half_cycles); }

static inline void clk_high(void) { gpio_put(SWD_PIN_SWCLK, 1); }
static inline void clk_low(void)  { gpio_put(SWD_PIN_SWCLK, 0); }

static inline void dio_out(int v)
{
    gpio_set_dir(SWD_PIN_SWDIO, GPIO_OUT);
    gpio_put(SWD_PIN_SWDIO, v ? 1 : 0);
}

static inline void dio_in(void)  { gpio_set_dir(SWD_PIN_SWDIO, GPIO_IN); }
static inline int  dio_get(void) { return gpio_get(SWD_PIN_SWDIO) ? 1 : 0; }

void swd_init_pins(void)
{
    gpio_init(SWD_PIN_SWCLK);
    gpio_set_dir(SWD_PIN_SWCLK, GPIO_OUT);
    gpio_put(SWD_PIN_SWCLK, 0);

    gpio_init(SWD_PIN_SWDIO);
    gpio_set_dir(SWD_PIN_SWDIO, GPIO_OUT);
    gpio_put(SWD_PIN_SWDIO, 1);

    gpio_init(SWD_PIN_NRESET);
    gpio_set_dir(SWD_PIN_NRESET, GPIO_OUT);
    gpio_put(SWD_PIN_NRESET, 1);      /* 低 = 复位 */
}

void swd_set_clock_khz(uint32_t khz)
{
    if (khz < 100)  khz = 100;
    if (khz > 4000) khz = 4000;
    uint32_t c = 125000000u / (2u * khz);
    if (c < 2) c = 2;
    s_half_cycles = c;
}

void swd_disconnect(void)
{
    dio_in();
    gpio_put(SWD_PIN_SWCLK, 0);
    gpio_put(SWD_PIN_NRESET, 1);
}

void swd_target_reset(void)
{
    gpio_put(SWD_PIN_NRESET, 0);
    sleep_ms(20);
    gpio_put(SWD_PIN_NRESET, 1);
    sleep_ms(20);
}

static void clock_cycles(int n)
{
    for (int i = 0; i < n; i++) { clk_high(); dly(); clk_low(); dly(); }
}

static void write_bits(uint32_t v, int n)
{
    for (int i = 0; i < n; i++) {
        dio_out((v >> i) & 1u);
        dly();
        clk_high();
        dly();
        clk_low();
    }
}

static uint32_t read_bits(int n)
{
    uint32_t v = 0;
    dio_in();
    dly();
    for (int i = 0; i < n; i++) {
        clk_high();
        dly();
        if (dio_get()) v |= (1u << i);
        clk_low();
        dly();
    }
    return v;
}

/* 线复位：SWDIO 高 + 56 个时钟 */
static void line_reset(void)
{
    dio_out(1);
    clock_cycles(56);
}

/* ARM 规定的 JTAG-to-SWD 切换序列 */
static void jtag_to_swd(void)
{
    dio_out(1);
    clock_cycles(56);
    write_bits(0xE79Eu, 16);
    dio_out(1);
    clock_cycles(56);
}

/*
 * 请求字节（ARM ADIv5）
 *   bit0 Start=1, bit1 APnDP, bit2 RnW, bit3 A2, bit4 A3,
 *   bit5 Parity(bit1..4), bit6 Stop=0, bit7 Park=1
 */
static uint8_t make_req(bool ap, bool read, uint8_t addr)
{
    uint32_t apndp = ap ? 1u : 0u;
    uint32_t rnw   = read ? 1u : 0u;
    uint32_t a2    = (addr >> 2) & 1u;
    uint32_t a3    = (addr >> 3) & 1u;
    uint32_t par   = (apndp + rnw + a2 + a3) & 1u;

    return (uint8_t)(1u | (apndp << 1) | (rnw << 2) | (a2 << 3) | (a3 << 4) |
                     (par << 5) | (1u << 7));
}

static uint8_t swd_transfer(uint8_t req, uint32_t *data, bool read)
{
    write_bits(req, 8);

    /* 转向周期：主机驱动 */
    dio_out(1);
    clock_cycles(1);

    uint32_t ack = read_bits(3);

    if (ack == ACK_OK) {
        if (read) {
            clock_cycles(1);                 /* TRN，目标接手驱动 */
            uint32_t d = read_bits(32);
            uint32_t p = read_bits(1);
            clock_cycles(1);                 /* TRN 回主机 */
            if ((uint32_t)__builtin_parity(d) != p) return (uint8_t)ACK_PARITY;
            *data = d;
        } else {
            dio_out(1);
            clock_cycles(1);                 /* TRN */
            write_bits(*data, 32);
            write_bits((uint32_t)__builtin_parity(*data), 1);
            clock_cycles(1);
        }
    } else {
        clock_cycles(1);
    }
    return (uint8_t)ack;
}

static bool dp_write(uint8_t addr, uint32_t v)
{
    return swd_transfer(make_req(false, false, addr), &v, false) == ACK_OK;
}

static bool dp_read(uint8_t addr, uint32_t *v)
{
    return swd_transfer(make_req(false, true, addr), v, true) == ACK_OK;
}

bool swd_connect(void)
{
    swd_init_pins();
    dly();

    for (int retry = 0; retry < 3; retry++) {
        line_reset();
        jtag_to_swd();
        line_reset();

        uint32_t id = 0;
        if (dp_read(0x00, &id) && id != 0) {
            dp_write(0x00, 0x1Eu);           /* ABORT: 清所有 sticky 标志 */
            dp_write(0x04, 0x50000000u);     /* CTRL/STAT: 请求系统与调试电源 */
            return true;
        }
    }
    return false;
}

/* ---------------- CMSIS-DAP 命令层 ---------------- */

static uint8_t s_connected = 0;

static uint32_t dap_info(uint8_t id, uint8_t *resp, uint32_t max)
{
    static const struct { uint8_t id; const char *s; } str_tbl[] = {
        { 0x01, "phicomm" },
        { 0x02, "Pico ESP Flasher DAP" },
        { 0x03, "000000000001" },
        { 0x04, "1.3.0" },
        { 0x05, "Generic" },
        { 0x06, "Cortex-M" },
        { 0x07, "phicomm" },
        { 0x08, "Pico" },
    };

    if (id == 0xF0) {                        /* Capabilities：只支持 SWD */
        resp[1] = 4;
        resp[2] = 0x01;                      /* bit0 = SWD */
        resp[3] = 0x00;
        resp[4] = 0x00;
        resp[5] = 0x00;
        return 6;
    }

    for (unsigned i = 0; i < sizeof(str_tbl) / sizeof(str_tbl[0]); i++) {
        if (str_tbl[i].id == id) {
            size_t l = strlen(str_tbl[i].s);
            if (l > max - 2) l = max - 2;
            resp[1] = (uint8_t)l;
            memcpy(&resp[2], str_tbl[i].s, l);
            return (uint32_t)(2 + l);
        }
    }

    resp[1] = 0;
    return 2;
}

static uint32_t dap_connect(uint8_t port, uint8_t *resp)
{
    if (port != 0 && port != 1) {            /* 只支持 SWD */
        resp[1] = 0x00;
        resp[2] = 0x00;
        return 3;
    }
    if (swd_connect()) {
        s_connected = 1;
        resp[1] = 0x01;                      /* SWD */
        resp[2] = 0x01;
    } else {
        s_connected = 0;
        resp[1] = 0x00;
        resp[2] = 0x00;
    }
    return 3;
}

static uint32_t dap_transfer(const uint8_t *req, uint32_t len,
                             uint8_t *resp, uint32_t max)
{
    /* 64 字节包：请求 3+cnt+4w，响应 2+cnt+4r，cnt 上限 12 */
    uint8_t cnt = req[2];
    if (cnt > 12) cnt = 12;
    if (cnt == 0 || len < (uint32_t)(3 + cnt)) { resp[1] = 0; return 2; }

    const uint8_t *rq = &req[3];
    const uint8_t *wd = &req[3 + cnt];
    uint8_t *rp = &resp[2];
    uint8_t *rd = &resp[2 + cnt];
    uint8_t done = 0;

    for (uint8_t i = 0; i < cnt; i++) {
        uint8_t r    = rq[i];
        bool    ap   = (r & 0x01) != 0;
        bool    rnw  = (r & 0x02) != 0;
        uint8_t addr = (uint8_t)(r & 0x0C);
        uint8_t ack;

        if (rnw) {
            uint32_t v = 0;
            ack = swd_transfer(make_req(ap, true, addr), &v, true);
            if (ack == ACK_OK) {
                if ((uint32_t)(rd - resp) + 4 <= max) memcpy(rd, &v, 4);
                rd += 4;
            }
        } else {
            uint32_t v = 0;
            memcpy(&v, wd, 4);
            wd += 4;
            ack = swd_transfer(make_req(ap, false, addr), &v, false);
        }

        *rp++ = (ack == ACK_OK) ? 0x01 : (ack == ACK_WAIT ? 0x02 : 0x04);
        done++;
        if (ack != ACK_OK) break;
    }

    resp[1] = done;
    uint32_t out = (uint32_t)(2 + done) + (uint32_t)(rd - &resp[2 + cnt]);
    if (out > max) out = max;
    return out;
}

static uint32_t dap_transfer_block(const uint8_t *req, uint32_t len,
                                   uint8_t *resp, uint32_t max)
{
    uint16_t cnt = 0;
    memcpy(&cnt, &req[2], 2);
    uint8_t r    = req[4];
    bool    ap   = (r & 0x01) != 0;
    bool    rnw  = (r & 0x02) != 0;
    uint8_t addr = (uint8_t)(r & 0x0C);

    /* 64 字节 USB 包：写时 5 + 4n，读时 4 + 4n */
    uint16_t limit = rnw ? (uint16_t)((max - 4) / 4) : (uint16_t)((max - 5) / 4);
    if (cnt > limit) cnt = limit;

    const uint8_t *wd = &req[5];
    uint8_t *rd = &resp[4];
    uint16_t done = 0;
    uint8_t ack = ACK_OK;

    for (uint16_t i = 0; i < cnt; i++) {
        if (rnw) {
            uint32_t v = 0;
            ack = swd_transfer(make_req(ap, true, addr), &v, true);
            if (ack == ACK_OK) { memcpy(rd, &v, 4); rd += 4; }
        } else {
            uint32_t v = 0;
            memcpy(&v, wd, 4);
            wd += 4;
            ack = swd_transfer(make_req(ap, false, addr), &v, false);
        }
        done++;
        if (ack != ACK_OK) break;
    }

    memcpy(&resp[1], &done, 2);
    resp[3] = (ack == ACK_OK) ? 0x01 : (ack == ACK_WAIT ? 0x02 : 0x04);
    uint32_t out = (uint32_t)(4 + (rd - &resp[4]));
    if (out > max) out = max;
    return out;
}

static uint32_t dap_swj_pins(const uint8_t *req, uint32_t len, uint8_t *resp)
{
    uint8_t val = req[1];
    uint8_t sel = req[2];
    uint32_t wait_us = 0;
    if (len >= 7) memcpy(&wait_us, &req[3], 4);

    /* bit0 SWCLK / bit1 SWDIO 由传输过程控制，这里只处理 nRESET(bit3) */
    if (sel & 0x08) gpio_put(SWD_PIN_NRESET, (val & 0x08) ? 1 : 0);
    if (wait_us) sleep_us(wait_us);

    uint8_t state = (uint8_t)(0x01 | 0x02 |
                    (gpio_get(SWD_PIN_NRESET) ? 0x08 : 0x00));
    resp[1] = 0x00;
    resp[2] = state;
    return 3;
}

static uint32_t dap_swj_sequence(const uint8_t *req, uint32_t len, uint8_t *resp)
{
    uint32_t nbits = req[1];
    if (nbits == 0) nbits = 256;

    for (uint32_t i = 0; i < nbits; i++) {
        uint32_t byte = i / 8;
        if (2u + byte >= len) break;
        int v = (req[2 + byte] >> (i % 8)) & 1;
        dio_out(v);
        dly();
        clk_high();
        dly();
        clk_low();
    }
    resp[1] = 0x00;
    return 2;
}

uint32_t dap_process(const uint8_t *req, uint32_t req_len,
                     uint8_t *resp, uint32_t resp_max)
{
    if (req_len < 1 || resp_max < 6) return 0;

    uint8_t cmd = req[0];
    resp[0] = cmd;

    switch (cmd) {
    case 0x00:                                   /* DAP_Info */
        return req_len >= 2 ? dap_info(req[1], resp, resp_max) : 2;

    case 0x01:                                   /* DAP_HostStatus */
        resp[1] = 0x00;
        return 2;

    case 0x02:                                   /* DAP_Connect */
        return req_len >= 2 ? dap_connect(req[1], resp) : 2;

    case 0x03:                                   /* DAP_Disconnect */
        s_connected = 0;
        swd_disconnect();
        resp[1] = 0x00;
        return 2;

    case 0x04:                                   /* DAP_TransferConfigure */
        resp[1] = 0x00;
        return 2;

    case 0x05:                                   /* DAP_Transfer */
        if (!s_connected) { resp[1] = 0; return 2; }
        return dap_transfer(req, req_len, resp, resp_max);

    case 0x06:                                   /* DAP_TransferBlock */
        if (!s_connected || req_len < 5) {
            resp[1] = 0; resp[2] = 0; resp[3] = 0x04;
            return 4;
        }
        return dap_transfer_block(req, req_len, resp, resp_max);

    case 0x07:                                   /* DAP_TransferAbort */
        resp[1] = 0x00;
        return 2;

    case 0x08: {                                 /* DAP_WriteABORT */
        uint32_t v = 0;
        if (req_len >= 6) memcpy(&v, &req[2], 4);
        resp[1] = dp_write(0x00, v) ? 0x00 : 0x01;
        return 2;
    }

    case 0x09: {                                 /* DAP_Delay */
        uint16_t us = 0;
        if (req_len >= 3) memcpy(&us, &req[1], 2);
        sleep_us(us);
        resp[1] = 0x00;
        return 2;
    }

    case 0x0A:                                   /* DAP_ResetTarget */
        swd_target_reset();
        swd_connect();
        resp[1] = 0x00;
        resp[2] = 0x01;
        return 3;

    case 0x10:                                   /* DAP_SWJ_Pins */
        return req_len >= 3 ? dap_swj_pins(req, req_len, resp) : 2;

    case 0x11: {                                 /* DAP_SWJ_Clock */
        uint32_t hz = 0;
        if (req_len >= 5) memcpy(&hz, &req[1], 4);
        uint32_t khz = hz / 1000u;
        if (khz == 0) khz = 1;
        swd_set_clock_khz(khz);
        resp[1] = 0x00;
        return 2;
    }

    case 0x12:                                   /* DAP_SWJ_Sequence */
        return req_len >= 2 ? dap_swj_sequence(req, req_len, resp) : 2;

    default:
        resp[1] = 0x02;                          /* 不支持 */
        return 2;
    }
}

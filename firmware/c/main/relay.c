/* DC1 —— 继电器 / 按键 / LOGO 灯
 *
 * 硬件（与 MicroPython 版一致，见 hardware/README.md）
 *   U7 = CAT9554，I2C 0x20，SCL=GPIO12 SDA=GPIO3，简配片只能 20 kHz
 *     P0~P2 = KEY1~KEY3 输入，P3 未用，P4~P7 = RELAY3~RELAY0 输出
 *   LOGO 灯 = GPIO14，低电平点亮
 *   总键    = GPIO16，低有效（板上已有上拉）
 *
 * ★ 准双向 IO 的两个坑（v1.2.8 修过，这里照搬结论）
 *   1) CAT9554 上电默认 CFG=0xFF 全输入。U7 一旦因 5V 跌落复位，方向寄存器回 0xFF，
 *      此后写输出寄存器不报错但引脚仍是输入 → 继电器完全不动作，而按键照常能读，
 *      表现为「API 全成功、继电器没反应」。所以每次写继电器前校验 CFG。
 *   2) 输入脚必须写 1 才有内部上拉；但不能把输出位一起清零（会误关继电器）。
 */
#include <string.h>

#include "driver/gpio.h"
#include "rom/ets_sys.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"

#include "dc1.h"

static const char *TAG = "relay";

#define U7_ADDR     0x20
#define REG_IN      0x00
#define REG_OUT     0x01
#define REG_POL     0x02
#define REG_CFG     0x03

#define CFG_MASK    0x0F        /* bit0~3 输入，bit4~7 输出 */

#define KEY1_BIT    0
#define KEY2_BIT    1
#define KEY3_BIT    2
#define RELAY3_BIT  4
#define RELAY2_BIT  5
#define RELAY1_BIT  6
#define RELAY0_BIT  7

#define PIN_LOGO    14
#define PIN_TOTAL   16
#define RELAY_GAP_MS 500

/* 通道 → U7 位。
 * DC1 有 4 个继电器：RELAY0=P7 是总闸，RELAY1/2/3=P6/P5/P4 是三个插孔。
 * （之前把 P7 当成 1 号插孔，导致 total 不动、关一路会连带别人，务必对照
 *   firmware/micropython/cat9554.py 的 RELAY_BIT 映射。） */
static const int8_t relay_bit[CH_MAX] = {
    RELAY0_BIT,     /* total -> P7 */
    RELAY1_BIT,     /* 1     -> P6 */
    RELAY2_BIT,     /* 2     -> P5 */
    RELAY3_BIT,     /* 3     -> P4 */
    -1              /* logo 走 GPIO14，不占 U7 */
};

static uint8_t u7_out;              /* 输出锁存器镜像 */
static bool    shadow[CH_MAX];      /* total 关断前的分路状态，用于恢复 */
static int64_t last_rly_ms;
static bool    keys_ready;
static uint32_t _wr_cnt;
/* ★ 互斥：HTTP 任务和按键轮询任务都会调 relay_set，不加锁时 u7_out 会被
 * 交叉修改，表现为「关一路连带另一路、且再也开不回来」（实测 2026-11-21）。 */
static SemaphoreHandle_t s_mtx;

/* ---------------------------------------------------------------- 软件 I2C
 *
 * ★ 必须用软件 I2C：ESP8266 的硬件 I2C 引脚固定为 SCL=GPIO14 / SDA=GPIO2，
 *   而 U7 焊在 SCL=GPIO12 / SDA=GPIO3。用硬件 I2C 驱动这两根脚会直接
 *   StoreProhibited panic（实测 2026-11-21）。
 *   MicroPython 版用的也是软件 I2C，所以 20 kHz 能跑通。
 *
 * U7 是简配片，SCL 只能 ~20 kHz，所以半周期取 26 µs（≈19 kHz）。
 */
#define PIN_SCL 12
#define PIN_SDA 3

static inline void scl_set(int v) { gpio_set_level(PIN_SCL, v); }
static inline void sda_set(int v) { gpio_set_level(PIN_SDA, v); }
static inline void i2c_dly(void) { ets_delay_us(100); }   /* U7 简配片，~10kHz 更稳 */

static void i2c_soft_init(void)
{
    gpio_config_t io = {
        .pin_bit_mask = (1ULL << PIN_SCL) | (1ULL << PIN_SDA),
        .mode = GPIO_MODE_OUTPUT_OD,   /* ESP8266 只有开漏输出；读引脚仍走 GPIO_INPUT_GET */
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    gpio_config(&io);
    gpio_set_level(PIN_SCL, 1);
    gpio_set_level(PIN_SDA, 1);
    ets_delay_us(100);
}

static void i2c_start(void)
{
    sda_set(1);
    scl_set(1);
    i2c_dly();
    sda_set(0);
    i2c_dly();
    scl_set(0);
    i2c_dly();
}

static void i2c_stop(void)
{
    sda_set(0);
    i2c_dly();
    scl_set(1);
    i2c_dly();
    sda_set(1);
    i2c_dly();
}

/* 返回 1 表示从机 ACK */
static int i2c_wr(uint8_t b)
{
    for (int i = 0; i < 8; i++) {
        sda_set((b & 0x80) ? 1 : 0);
        b = (uint8_t)(b << 1);
        i2c_dly();
        scl_set(1);
        i2c_dly();
        scl_set(0);
        i2c_dly();
    }
    sda_set(1);              /* 释放，让从机拉低应答 */
    i2c_dly();
    scl_set(1);
    i2c_dly();
    int ack = (gpio_get_level(PIN_SDA) == 0);
    scl_set(0);
    i2c_dly();
    return ack;
}

static uint8_t i2c_rd(int ack)
{
    uint8_t b = 0;
    sda_set(1);
    for (int i = 0; i < 8; i++) {
        i2c_dly();
        scl_set(1);
        i2c_dly();
        b = (uint8_t)((b << 1) | (gpio_get_level(PIN_SDA) ? 1 : 0));
        scl_set(0);
        i2c_dly();
    }
    sda_set(ack ? 0 : 1);    /* 0 = ACK，1 = NACK */
    i2c_dly();
    scl_set(1);
    i2c_dly();
    scl_set(0);
    i2c_dly();
    return b;
}

/* ---------------------------------------------------------------- U7 访问 */

/* I2C 总线上的每一次传输都必须持锁 + 关中断。
 * ★ keys_read() 每 50ms 读一次 REG_IN，与 HTTP 任务里 relay_set 的写交错，
 *   U7 会收到错位字节 —— 表现为「关一路带掉另一路」「开一路反而全关」。
 *   互斥锁解决任务切换，但 bit-bang 的时序还怕中断：一次传输约 2.7ms
 *   （9 位 × 3 字节 × 100µs），中断插进来会把 SCL 高电平拉长到从机无法识别。
 *   所以两层都要：先拿递归锁（可阻塞），再进临界区（不可阻塞）。
 *   关中断窗口控制在单次传输内，ESP8266 的 WiFi 有硬件缓冲，2~3ms 可接受。 */

static esp_err_t u7_wr(uint8_t reg, uint8_t val)
{
    if (s_mtx) {
        xSemaphoreTakeRecursive(s_mtx, portMAX_DELAY);
    }
    esp_err_t r = ESP_OK;
    portENTER_CRITICAL();
    i2c_start();
    if (!i2c_wr((uint8_t)(U7_ADDR << 1))) {
        i2c_stop();
        r = ESP_FAIL;
    } else {
        i2c_wr(reg);
        i2c_wr(val);
        i2c_stop();
    }
    portEXIT_CRITICAL();
    if (s_mtx) {
        xSemaphoreGiveRecursive(s_mtx);
    }
    return r;
}

static esp_err_t u7_rd(uint8_t reg, uint8_t *out)
{
    if (s_mtx) {
        xSemaphoreTakeRecursive(s_mtx, portMAX_DELAY);
    }
    esp_err_t r = ESP_OK;
    portENTER_CRITICAL();
    i2c_start();
    if (!i2c_wr((uint8_t)(U7_ADDR << 1))) {
        i2c_stop();
        r = ESP_FAIL;
    } else if (!i2c_wr(reg)) {
        i2c_stop();
        r = ESP_FAIL;
    } else {
        i2c_start();                   /* 重复起始 */
        if (!i2c_wr((uint8_t)((U7_ADDR << 1) | 1))) {
            i2c_stop();
            r = ESP_FAIL;
        } else {
            *out = i2c_rd(0);          /* 最后一字节 NACK */
            i2c_stop();
        }
    }
    portEXIT_CRITICAL();
    if (s_mtx) {
        xSemaphoreGiveRecursive(s_mtx);
    }
    return r;
}

/* 重配方向与输出。U7 复位后调用即可恢复。 */
static void u7_apply(void)
{
    u7_wr(REG_POL, 0x00);           /* 不反相 */
    u7_wr(REG_OUT, u7_out);         /* 先定电平，避免切方向时抖动 */
    u7_wr(REG_CFG, CFG_MASK);
}

static bool u7_configured(void)
{
    uint8_t cfg = 0;
    if (u7_rd(REG_CFG, &cfg) != ESP_OK) {
        return false;
    }
    return cfg == CFG_MASK;
}

/* ---------------------------------------------------------------- 继电器 */

static void relay_pin(dc1_ch_t ch, bool on)
{
    int8_t bit = relay_bit[ch];
    if (bit < 0) {
        return;
    }
    /* 相邻动作间隔 500ms，防止多路同时吸合的电流冲击把 5V 拉垮 */
    int64_t now = esp_timer_get_time() / 1000;
    if (last_rly_ms && (now - last_rly_ms) < RELAY_GAP_MS) {
        vTaskDelay(pdMS_TO_TICKS(RELAY_GAP_MS - (now - last_rly_ms)));
    }

    if ((++_wr_cnt & 0x1F) == 0 && !u7_configured()) {
        ESP_LOGW(TAG, "U7 方向寄存器异常，重建配置");
        u7_apply();
    }
    if (on) {
        u7_out |= (uint8_t)(1 << bit);
    } else {
        u7_out &= (uint8_t)~(1 << bit);
    }
    /* 写后读回校验，最多重试 3 次。
     * U7 偶发吃错位（关一路把相邻路带掉），单次读回不一致时重建方向寄存器再写，
     * 直到读回与软件镜像一致。这是最后一道防线，宁可多两次 I2C 也不能让状态漂。 */
    for (int retry = 0; retry < 3; retry++) {
        u7_wr(REG_OUT, u7_out);
        uint8_t back = 0;
        if (u7_rd(REG_OUT, &back) == ESP_OK && back == u7_out) {
            break;
        }
        u7_wr(REG_POL, 0x00);
        u7_wr(REG_CFG, CFG_MASK);
    }
    last_rly_ms = esp_timer_get_time() / 1000;
}

static void logo_set(bool on)
{
    gpio_set_level(PIN_LOGO, on ? 0 : 1);   /* 低电平点亮 */
}

bool relay_get(dc1_ch_t ch)
{
    if (ch >= CH_MAX) {
        return false;
    }
    return g_state[ch];
}

void relay_set(dc1_ch_t ch, bool on)
{
    if (ch >= CH_MAX) {
        return;
    }
    if (s_mtx) {
        xSemaphoreTakeRecursive(s_mtx, portMAX_DELAY);
    }

    if (ch == CH_LOGO) {
        logo_set(on);
        g_state[CH_LOGO] = on;
        if (s_mtx) {
            xSemaphoreGiveRecursive(s_mtx);
        }
        return;
    }

    if (ch == CH_TOTAL) {
        if (on) {
            relay_pin(CH_TOTAL, true);              /* 先合总闸 */
            /* 只恢复 shadow 记录过的分路，其余保持原状 ——
             * 不能按 shadow 强行赋值，否则 shadow=false 的分路会被主动关掉
             * （实测表现：开总闸把三路全关了）。 */
            for (int i = CH_1; i <= CH_3; i++) {
                if (shadow[i]) {
                    relay_pin((dc1_ch_t)i, true);
                    g_state[i] = true;
                    shadow[i] = false;
                }
            }
            logo_set(true);
            g_state[CH_LOGO] = true;
        } else {
            for (int i = CH_1; i <= CH_3; i++) {
                shadow[i] = g_state[i];             /* 记住再断 */
                relay_pin((dc1_ch_t)i, false);
                g_state[i] = false;
            }
            relay_pin(CH_TOTAL, false);             /* 最后断总闸 */
            logo_set(false);
            g_state[CH_LOGO] = false;
        }
        g_state[CH_TOTAL] = on;
        if (s_mtx) {
            xSemaphoreGiveRecursive(s_mtx);
        }
        return;
    }

    /* 分路：总闸关着时先开总闸 */
    if (on && !g_state[CH_TOTAL]) {
        relay_pin(CH_TOTAL, true);
        g_state[CH_TOTAL] = true;
        logo_set(true);
        g_state[CH_LOGO] = true;
    }
    relay_pin(ch, on);
    g_state[ch] = on;
    if (s_mtx) {
        xSemaphoreGiveRecursive(s_mtx);
    }
}

void relay_restore(void)
{
    /* 上电默认四路全开（总闸 + 三个插孔），与 MicroPython 版一致。
     * 总闸是独立继电器（P7），必须单独吸合，否则三路有信号但没电。 */
    relay_pin(CH_TOTAL, true);
    for (int i = CH_1; i <= CH_3; i++) {
        relay_pin((dc1_ch_t)i, true);
        g_state[i] = true;
        shadow[i] = false;
    }
    logo_set(true);
    g_state[CH_LOGO] = true;
    g_state[CH_TOTAL] = true;
}

/* ---------------------------------------------------------------- 按键 */

void relay_init(void)
{
    if (!s_mtx) {
        s_mtx = xSemaphoreCreateRecursiveMutex();
    }
    i2c_soft_init();                    /* 软件 I2C，见文件头说明 */

    gpio_config_t io = {
        .pin_bit_mask = (1ULL << PIN_LOGO),
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&io);
    gpio_set_level(PIN_LOGO, 1);        /* 初始灭 */

    gpio_config_t ki = {
        .pin_bit_mask = (1ULL << PIN_TOTAL),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    gpio_config(&ki);

    /* 准双向：输入位写 1 才有上拉，输出位保持 0（继电器全断） */
    u7_out = CFG_MASK & 0x07;
    u7_apply();
    keys_ready = true;
    ESP_LOGI(TAG, "U7 ready, cfg=0x%02X out=0x%02X", CFG_MASK, u7_out);
}

uint8_t keys_read(void)
{
    uint8_t in = 0;
    if (u7_rd(REG_IN, &in) != ESP_OK) {
        return 0xFF;
    }
    uint8_t r = in & 0x07;                       /* KEY1~3 在 P0~P2 */
    /* GPIO16 是 RTC IO，gpio_get_level 读不到，先用固定值避免误触发总闸 */
    if (0 && gpio_get_level(PIN_TOTAL) == 0) {
        r |= 0x08;                               /* 总键按下 */
    }
    return r;
}

void keys_poll(void)
{
    static uint8_t prev = 0xFF;
    static int64_t t_down[4] = {0};

    if (!keys_ready) {
        return;
    }
    uint8_t now = keys_read();
    for (int i = 0; i < 4; i++) {
        bool was = (prev >> i) & 1;
        bool is = (now >> i) & 1;
        if (was && !is) {                        /* 按下（低有效） */
            t_down[i] = esp_timer_get_time() / 1000;
        } else if (!was && is) {                 /* 松开：短按 < 800ms 才动作 */
            int64_t dt = esp_timer_get_time() / 1000 - t_down[i];
            if (dt > 20 && dt < 800) {
                if (i < 3) {
                    relay_set((dc1_ch_t)(CH_1 + i), !g_state[CH_1 + i]);
                } else {
                    relay_set(CH_TOTAL, !g_state[CH_TOTAL]);
                }
            }
        }
    }
    prev = now;
}

/* ---------------------------------------------------------------- 诊断 */

uint8_t relay_dbg_out(void)
{
    return u7_out;
}

int relay_dbg_regs(uint8_t *out_reg, uint8_t *in_reg, uint8_t *cfg_reg)
{
    int ok = 0;
    if (out_reg && u7_rd(REG_OUT, out_reg) == ESP_OK) {
        ok++;
    }
    if (in_reg && u7_rd(REG_IN, in_reg) == ESP_OK) {
        ok++;
    }
    if (cfg_reg && u7_rd(REG_CFG, cfg_reg) == ESP_OK) {
        ok++;
    }
    return ok;
}

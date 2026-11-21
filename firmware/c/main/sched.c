/* DC1 —— 定时任务与倒计时
 *
 * 时间基准
 *   C 版用标准 epoch（UTC）+ TZ=CST-8，所以 time() 是真 UTC，localtime_r() 是北京时间。
 *   这比 MicroPython 版干净 —— 那边是把 RTC 存成北京时间，导致 /api/state.time 比标准
 *   epoch 大 28800，客户端必须自己减掉。
 *
 * 持久化
 *   整个数组以 blob 存 NVS，改动即提交。
 */
#include <string.h>
#include <time.h>

#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "dc1.h"

static const char *TAG = "sched";
static const char *NVS_NS = "dc1";

static sched_rule_t g_rules[SCHED_MAX];
static cd_item_t    g_cd[CD_MAX];
static uint8_t      g_next_rule = 1;
static uint8_t      g_next_cd = 1;
static char         g_last_fire[24];      /* "MM-DD HH:MM" 去重 */

/* ---------------------------------------------------------------- 持久化 */

static void sched_save(void)
{
    nvs_handle h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) != ESP_OK) {
        return;
    }
    nvs_set_blob(h, "rules", g_rules, sizeof(g_rules));
    nvs_set_u8(h, "rnext", g_next_rule);
    nvs_commit(h);
    nvs_close(h);
}

static void cd_save(void)
{
    nvs_handle h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) != ESP_OK) {
        return;
    }
    nvs_set_blob(h, "cds", g_cd, sizeof(g_cd));
    nvs_set_u8(h, "cnext", g_next_cd);
    nvs_commit(h);
    nvs_close(h);
}

void sched_init(void)
{
    memset(g_rules, 0, sizeof(g_rules));
    memset(g_cd, 0, sizeof(g_cd));

    nvs_handle h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) == ESP_OK) {
        size_t len = sizeof(g_rules);
        nvs_get_blob(h, "rules", g_rules, &len);
        nvs_get_u8(h, "rnext", &g_next_rule);
        len = sizeof(g_cd);
        nvs_get_blob(h, "cds", g_cd, &len);
        nvs_get_u8(h, "cnext", &g_next_cd);
        nvs_close(h);
    }
    if (g_next_rule == 0) {
        g_next_rule = 1;
    }
    if (g_next_cd == 0) {
        g_next_cd = 1;
    }
    ESP_LOGI(TAG, "sched=%d cd=%d", sched_count(), cd_count());
}

/* ---------------------------------------------------------------- 定时规则 */

int sched_count(void)
{
    int n = 0;
    for (int i = 0; i < SCHED_MAX; i++) {
        if (g_rules[i].used) {
            n++;
        }
    }
    return n;
}

int sched_list(sched_rule_t *out, int max)
{
    int n = 0;
    for (int i = 0; i < SCHED_MAX && n < max; i++) {
        if (g_rules[i].used) {
            out[n++] = g_rules[i];
        }
    }
    return n;
}

int sched_add(int ch, int on, int hh, int mm, int days)
{
    for (int i = 0; i < SCHED_MAX; i++) {
        if (g_rules[i].used) {
            continue;
        }
        sched_rule_t *r = &g_rules[i];
        memset(r, 0, sizeof(*r));
        r->used = 1;
        r->id = g_next_rule++;
        r->ch = (uint8_t)ch;
        r->on = on ? 1 : 0;
        r->hh = (uint8_t)(hh % 24);
        r->mm = (uint8_t)(mm % 60);
        r->days = (uint8_t)(days & 0x7F);
        r->en = 1;
        sched_save();
        return r->id;
    }
    return -1;
}

int sched_del(int id)
{
    for (int i = 0; i < SCHED_MAX; i++) {
        if (g_rules[i].used && g_rules[i].id == id) {
            memset(&g_rules[i], 0, sizeof(g_rules[i]));
            sched_save();
            return 1;
        }
    }
    return 0;
}

int sched_enable(int id, int en)
{
    for (int i = 0; i < SCHED_MAX; i++) {
        if (g_rules[i].used && g_rules[i].id == id) {
            g_rules[i].en = en ? 1 : 0;
            sched_save();
            return 1;
        }
    }
    return 0;
}

void sched_clear(void)
{
    memset(g_rules, 0, sizeof(g_rules));
    sched_save();
}

void sched_poll(void)
{
    time_t now = time(NULL);
    if (now < 1600000000) {
        return;                     /* SNTP 还没同步 */
    }
    struct tm tm;
    localtime_r(&now, &tm);

    char key[24];
    snprintf(key, sizeof(key), "%02d-%02d %02d:%02d",
             tm.tm_mon + 1, tm.tm_mday, tm.tm_hour, tm.tm_min);
    if (strcmp(key, g_last_fire) == 0) {
        return;                     /* 同一分钟只触发一次 */
    }
    strlcpy(g_last_fire, key, sizeof(g_last_fire));

    /* tm_wday: 0=周日。规则 bit0=周一，所以换算一下 */
    int dow_bit = (tm.tm_wday == 0) ? 6 : (tm.tm_wday - 1);

    for (int i = 0; i < SCHED_MAX; i++) {
        sched_rule_t *r = &g_rules[i];
        if (!r->used || !r->en) {
            continue;
        }
        if (r->hh != tm.tm_hour || r->mm != tm.tm_min) {
            continue;
        }
        if (r->days != 0 && !((r->days >> dow_bit) & 1)) {
            continue;
        }
        ESP_LOGI(TAG, "rule %u -> ch=%u on=%u", r->id, r->ch, r->on);
        relay_set((dc1_ch_t)r->ch, r->on);
        if (r->days == 0) {         /* 单次任务，触发后自删 */
            memset(r, 0, sizeof(*r));
            sched_save();
        }
    }
}

/* ---------------------------------------------------------------- 倒计时 */

int cd_count(void)
{
    int n = 0;
    for (int i = 0; i < CD_MAX; i++) {
        if (g_cd[i].used) {
            n++;
        }
    }
    return n;
}

int cd_list(cd_item_t *out, int max)
{
    int n = 0;
    for (int i = 0; i < CD_MAX && n < max; i++) {
        if (g_cd[i].used) {
            out[n++] = g_cd[i];
        }
    }
    return n;
}

int cd_add(int ch, int on, int sec)
{
    if (sec <= 0) {
        return -1;
    }
    for (int i = 0; i < CD_MAX; i++) {
        if (g_cd[i].used) {
            continue;
        }
        cd_item_t *it = &g_cd[i];
        memset(it, 0, sizeof(*it));
        it->used = 1;
        it->id = g_next_cd++;
        it->ch = (uint8_t)ch;
        it->on = on ? 1 : 0;
        it->left_ms = (uint32_t)sec * 1000u;
        cd_save();
        return it->id;
    }
    return -1;
}

int cd_del(int id)
{
    for (int i = 0; i < CD_MAX; i++) {
        if (g_cd[i].used && g_cd[i].id == id) {
            memset(&g_cd[i], 0, sizeof(g_cd[i]));
            cd_save();
            return 1;
        }
    }
    return 0;
}

void cd_clear(void)
{
    memset(g_cd, 0, sizeof(g_cd));
    cd_save();
}

void cd_poll(void)
{
    static int64_t last = 0;
    int64_t now = esp_timer_get_time() / 1000;
    if (last == 0) {
        last = now;
        return;
    }
    uint32_t dt = (uint32_t)(now - last);
    last = now;

    bool dirty = false;
    for (int i = 0; i < CD_MAX; i++) {
        cd_item_t *it = &g_cd[i];
        if (!it->used) {
            continue;
        }
        if (it->left_ms > dt) {
            it->left_ms -= dt;
        } else {
            ESP_LOGI(TAG, "cd %u fired ch=%u on=%u", it->id, it->ch, it->on);
            relay_set((dc1_ch_t)it->ch, it->on);
            memset(it, 0, sizeof(*it));
            dirty = true;
        }
    }
    if (dirty) {
        cd_save();
    }
}

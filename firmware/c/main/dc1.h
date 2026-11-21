/* DC1 C 版固件 —— 公共定义
 *
 * 与 MicroPython 版功能对齐，但用 C 重写：
 *   - 不受 16,205 B 的 mpy 体积上限约束（那是 1 MB 版 MicroPython 的堆限制）
 *   - 内存占用从 ~30 KB 降到几 KB
 *   - OTA 可做真正的固件级升级
 */
#ifndef DC1_H
#define DC1_H

#include <stdbool.h>
#include <stdint.h>

#define FW_VERSION   "2.0.0"
#define TYPE_NAME    "DC1"

/* ---------------- 通道 ---------------- */
typedef enum {
    CH_TOTAL = 0,
    CH_1,
    CH_2,
    CH_3,
    CH_LOGO,
    CH_MAX
} dc1_ch_t;

/* 通道名（与 API 一致：total/1/2/3/logo） */
extern const char *CH_NAME[CH_MAX];

/* ---------------- 配置 ---------------- */
typedef struct {
    char ssid[33];
    char pass[65];
    char name[33];
    char ap_ssid[33];
    char token[33];
    uint16_t http_port;
    bool   ap_mode;      /* 本次启动是否因无 SSID 进了 AP 配网 */
} dc1_cfg_t;

extern dc1_cfg_t g_cfg;
extern bool      g_state[CH_MAX];   /* 当前开关状态 */

/* ---------------- 配置（NVS） ---------------- */
void cfg_load(void);
esp_err_t cfg_save(void);

/* ---------------- 继电器 / 按键 ---------------- */
void relay_init(void);
void relay_set(dc1_ch_t ch, bool on);   /* 含 total 联动、logo */
bool relay_get(dc1_ch_t ch);
void relay_restore(void);               /* 上电按 state 恢复 */
uint8_t keys_read(void);                /* bit0~2 = KEY1~3, bit3 = 总键 */
void keys_poll(void);                   /* 按键扫描 + 短按动作 */

/* 诊断（/api/raw 用） */
uint8_t relay_dbg_out(void);
int relay_dbg_regs(uint8_t *out_reg, uint8_t *in_reg, uint8_t *cfg_reg);

/* ---------------- 定时 / 倒计时 ---------------- */
typedef struct {
    uint8_t  used;
    uint8_t  id;
    uint8_t  ch;
    uint8_t  on;
    uint8_t  hh;
    uint8_t  mm;
    uint8_t  days;      /* bit0=周一 … bit6=周日；0=单次 */
    uint8_t  en;
} sched_rule_t;

typedef struct {
    uint8_t  used;
    uint8_t  id;
    uint8_t  ch;
    uint8_t  on;
    uint32_t left_ms;
} cd_item_t;

#define SCHED_MAX 16
#define CD_MAX     8

void sched_init(void);
void sched_poll(void);
void cd_poll(void);

int  sched_count(void);
int  sched_list(sched_rule_t *out, int max);
int  sched_add(int ch, int on, int hh, int mm, int days);
int  sched_del(int id);
int  sched_enable(int id, int en);
void sched_clear(void);

int  cd_count(void);
int  cd_list(cd_item_t *out, int max);
int  cd_add(int ch, int on, int sec);
int  cd_del(int id);
void cd_clear(void);

/* ---------------- HTTP ---------------- */
void httpd_start_all(void);

/* 由 httpd 提供、给 main 用的状态快照 */
char *state_json(void);     /* 返回静态缓冲，调用后需在下次调用前用完 */

/* ---------------- 系统 ---------------- */
void dc1_reboot(const char *why);

#endif /* DC1_H */

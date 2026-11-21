/* DC1 C 版固件 —— 入口
 *
 * 启动顺序（顺序有讲究）
 *   1. NVS  -> 2. 配置 -> 3. 继电器/LOGO -> 4. 定时/倒计时
 *   5. 时区 -> 6. WiFi（STA 或 AP）-> 7. HTTP -> 8. 主循环
 *
 * 继电器必须在 HTTP 起来之前恢复，否则网页第一次拉 state 会和继电器实际状态不一致。
 */
#include <string.h>
#include <time.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/apps/sntp.h"
#include "nvs_flash.h"
#include "tcpip_adapter.h"

#include "dc1.h"

static const char *TAG = "dc1";

static volatile bool s_got_ip;
static volatile bool s_restarting;

/* ---------------------------------------------------------------- 事件 */

static void on_wifi(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT) {
        if (id == WIFI_EVENT_STA_START) {
            esp_wifi_connect();
        } else if (id == WIFI_EVENT_STA_DISCONNECTED) {
            if (!s_restarting) {
                ESP_LOGW(TAG, "WiFi 断开，2s 后重连");
                vTaskDelay(pdMS_TO_TICKS(2000));
                esp_wifi_connect();
            }
        } else if (id == WIFI_EVENT_AP_STACONNECTED) {
            ESP_LOGI(TAG, "有设备连上配网热点");
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = (ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "got ip " IPSTR, IP2STR(&e->ip_info.ip));
        if (!s_got_ip) {
            s_got_ip = true;
            sntp_setoperatingmode(SNTP_OPMODE_POLL);
            sntp_setservername(0, "ntp.aliyun.com");
            sntp_setservername(1, "ntp1.aliyun.com");
            sntp_init();
        }
    }
}

/* ---------------------------------------------------------------- WiFi */

static void wifi_start(void)
{
    wifi_init_config_t ic = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&ic));

    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                               on_wifi, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                               on_wifi, NULL));

    if (g_cfg.ssid[0]) {
        wifi_config_t wc = {0};
        strlcpy((char *)wc.sta.ssid, g_cfg.ssid, sizeof(wc.sta.ssid));
        strlcpy((char *)wc.sta.password, g_cfg.pass, sizeof(wc.sta.password));
        g_cfg.ap_mode = false;
        ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
        ESP_ERROR_CHECK(esp_wifi_set_config(ESP_IF_WIFI_STA, &wc));
        ESP_LOGI(TAG, "STA -> %s", g_cfg.ssid);
    } else {
        wifi_config_t wc = {0};
        strlcpy((char *)wc.ap.ssid, g_cfg.ap_ssid, sizeof(wc.ap.ssid));
        wc.ap.ssid_len = strlen(g_cfg.ap_ssid);
        wc.ap.authmode = WIFI_AUTH_OPEN;
        wc.ap.max_connection = 4;
        g_cfg.ap_mode = true;
        ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
        ESP_ERROR_CHECK(esp_wifi_set_config(ESP_IF_WIFI_AP, &wc));
        ESP_LOGI(TAG, "AP '%s' 192.168.4.1（无 SSID，进配网模式）",
                 g_cfg.ap_ssid);
    }
    ESP_ERROR_CHECK(esp_wifi_start());
}

/* ---------------------------------------------------------------- 重启 */

void dc1_reboot(const char *why)
{
    ESP_LOGW(TAG, "reboot: %s", why);
    s_restarting = true;
    /* C 版不需要像 MicroPython 那样等 LittleFS 落盘：配置走 NVS，
     * 每次写入都已 commit，不存在文件系统元数据悬挂问题。 */
    vTaskDelay(pdMS_TO_TICKS(400));
    esp_restart();
}

/* ---------------------------------------------------------------- main */

void app_main(void)
{
    ESP_LOGI(TAG, "DC1 C 固件 %s 启动", FW_VERSION);

    esp_err_t err = nvs_flash_init();
    if (err != ESP_OK) {
        nvs_flash_erase();
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    cfg_load();
    relay_init();
    relay_restore();
    sched_init();

    /* 时区：RTC 走 UTC，localtime_r 才给北京时间。
     * 这点和 MicroPython 版不同 —— 那边把 RTC 直接存北京时间，
     * 导致 /api/state.time 比标准 epoch 大 28800。 */
    setenv("TZ", "CST-8", 1);
    tzset();

    /* 顺序不能动：tcpip_adapter_init() 必须在事件循环和 WiFi 之前，
     * 否则 lwIP 的 tcpip 邮箱没建，一调 WiFi 就
     * `assertion "Invalid mbox" failed` 走 abort。 */
    tcpip_adapter_init();
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    wifi_start();
    httpd_start_all();

    ESP_LOGI(TAG, "free heap = %u", (unsigned)esp_get_free_heap_size());

    while (1) {
        keys_poll();
        sched_poll();
        cd_poll();
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

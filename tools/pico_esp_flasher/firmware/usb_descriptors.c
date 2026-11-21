/*
 * usb_descriptors.c - 三路 CDC 复合设备
 *
 *   ITF0/1 = CDC0 数据口 -> UART0 (GP0 TX / GP1 RX)
 *   ITF2/3 = CDC1 数据口 -> UART1 (GP8 TX / GP9 RX)
 *   ITF4/5 = CDC2 控制口  ASCII 命令
 *
 * 端点预算（每路 3 个，共 9 个 / RP2040 上限 15）
 *   CDC0: OUT 0x01 / IN 0x81 / notify 0x82
 *   CDC1: OUT 0x03 / IN 0x83 / notify 0x84
 *   CDC2: OUT 0x05 / IN 0x85 / notify 0x86
 */

#include <string.h>

#include "tusb.h"

#ifndef USB_VID
#define USB_VID   0x2E8A
#endif
#ifndef USB_PID
#define USB_PID   0x1001
#endif

/* ---------------- 设备描述符 ---------------- */

tusb_desc_device_t const desc_device = {
    .bLength            = sizeof(tusb_desc_device_t),
    .bDescriptorType    = TUSB_DESC_DEVICE,
    .bcdUSB             = 0x0200,

    /* 复合设备：TUD_CDC_DESCRIPTOR 自带 IAD，设备类必须是 MISC/IAD */
    .bDeviceClass       = TUSB_CLASS_MISC,
    .bDeviceSubClass    = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol    = MISC_PROTOCOL_IAD,

    .bMaxPacketSize0    = CFG_TUD_ENDPOINT0_SIZE,

    .idVendor           = USB_VID,
    .idProduct          = USB_PID,
    .bcdDevice          = 0x0110,

    .iManufacturer      = 0x01,
    .iProduct           = 0x02,
    .iSerialNumber      = 0x03,

    .bNumConfigurations = 0x01,
};

uint8_t const *tud_descriptor_device_cb(void)
{
    return (uint8_t const *)&desc_device;
}

/* ---------------- 配置描述符 ---------------- */

enum {
    ITF_NUM_CDC0 = 0,
    ITF_NUM_CDC0_DATA,
    ITF_NUM_CDC1,
    ITF_NUM_CDC1_DATA,
    ITF_NUM_CDC2,
    ITF_NUM_CDC2_DATA,
#ifdef WITH_DAP
    ITF_NUM_DAP,
#endif
    ITF_NUM_TOTAL
};

#ifdef WITH_DAP
#define CONFIG_TOTAL_LEN  (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN * 3 + TUD_VENDOR_DESC_LEN)
#else
#define CONFIG_TOTAL_LEN  (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN * 3)
#endif

#define EPNUM_CDC0_NOTIF   0x82
#define EPNUM_CDC0_OUT     0x01
#define EPNUM_CDC0_IN      0x81
#define EPNUM_CDC1_NOTIF   0x84
#define EPNUM_CDC1_OUT     0x03
#define EPNUM_CDC1_IN      0x83
#define EPNUM_CDC2_NOTIF   0x86
#define EPNUM_CDC2_OUT     0x05
#define EPNUM_CDC2_IN      0x85

/* CMSIS-DAP v2 用 Vendor Bulk（WinUSB / libusb 访问） */
#define EPNUM_DAP_OUT      0x02
#define EPNUM_DAP_IN       0x8A

uint8_t const desc_configuration[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, 0x00, 500),

    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC0, 4, EPNUM_CDC0_NOTIF, 8,
                       EPNUM_CDC0_OUT, EPNUM_CDC0_IN, 64),
    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC1, 5, EPNUM_CDC1_NOTIF, 8,
                       EPNUM_CDC1_OUT, EPNUM_CDC1_IN, 64),
    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC2, 6, EPNUM_CDC2_NOTIF, 8,
                       EPNUM_CDC2_OUT, EPNUM_CDC2_IN, 64),
#ifdef WITH_DAP
    TUD_VENDOR_DESCRIPTOR(ITF_NUM_DAP, 7, EPNUM_DAP_OUT, EPNUM_DAP_IN, 64),
#endif
};

uint8_t const *tud_descriptor_configuration_cb(uint8_t index)
{
    (void)index;
    return desc_configuration;
}

/* ---------------- 字符串描述符 ---------------- */

static char const *const string_desc_arr[] = {
    (const char[]){0x09, 0x04},   /* 0: English (US) */
    "phicomm-tools",              /* 1: Manufacturer */
    "Pico ESP Flasher",           /* 2: Product */
    "000000000001",               /* 3: Serial */
    "UART0",                      /* 4: CDC0 数据口 */
    "UART1",                      /* 5: CDC1 数据口 */
    "Ctrl",                       /* 6: CDC2 控制口 */
    "CMSIS-DAP",                  /* 7: DAP 接口 */
};

uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid)
{
    (void)langid;
    static uint16_t desc_str[32];
    uint8_t chr_count;

    if (index == 0) {
        memcpy(&desc_str[1], string_desc_arr[0], 2);
        chr_count = 1;
    } else {
        if (index >= sizeof(string_desc_arr) / sizeof(string_desc_arr[0])) {
            return NULL;
        }
        char const *str = string_desc_arr[index];
        chr_count = (uint8_t)strlen(str);
        if (chr_count > 31) chr_count = 31;
        for (uint8_t i = 0; i < chr_count; i++) {
            desc_str[1 + i] = (uint16_t)(uint8_t)str[i];
        }
    }

    desc_str[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * chr_count + 2));
    return desc_str;
}

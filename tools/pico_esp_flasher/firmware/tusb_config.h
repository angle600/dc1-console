/*
 * tusb_config.h - 双 CDC 配置
 *   ITF0 = 数据桥（给 esptool，绝不打印任何字符）
 *   ITF1 = 控制台（ASCII 命令，电源 / IO0 / EN / 波特率）
 *
 * 本文件必须优先于 pico-sdk 自带的 src/rp2_common/tinyusb/include/tusb_config.h
 */
#ifndef _TUSB_CONFIG_H_
#define _TUSB_CONFIG_H_

#ifdef __cplusplus
extern "C" {
#endif

#ifndef CFG_TUSB_MCU
#define CFG_TUSB_MCU              OPT_MCU_RP2040
#endif
#ifndef CFG_TUSB_OS
#define CFG_TUSB_OS               OPT_OS_PICO
#endif

#define CFG_TUSB_DEBUG            0
#define CFG_TUSB_MEM_SECTION
#define CFG_TUSB_MEM_ALIGN        __attribute__ ((aligned(4)))

/* USB 身份：沿用树莓派 VID，PID 用自定义值，上位机据此精确识别两个 COM 口 */
#ifndef USB_VID
#define USB_VID                   0x2E8A
#endif
#ifndef USB_PID
#define USB_PID                   0x1001
#endif
#ifndef USB_MANUFACTURER
#define USB_MANUFACTURER          "phicomm-tools"
#endif
#ifndef USB_PRODUCT
#define USB_PRODUCT               "Pico ESP Flasher"
#endif

/*------------- Device --------------*/
#ifndef CFG_TUD_ENABLED
#define CFG_TUD_ENABLED           1
#endif
#ifndef CFG_TUD_MAX_SPEED
#define CFG_TUD_MAX_SPEED         OPT_MODE_FULL_SPEED
#endif

/* 关键：不定义这一项时 TinyUSB 默认 OPT_MODE_NONE，USB 控制器完全不启动，
   Windows 侧连设备节点都不会出现（表现为"烧完就没反应"）。 */
#ifndef CFG_TUSB_RHPORT0_MODE
#define CFG_TUSB_RHPORT0_MODE     OPT_MODE_DEVICE
#endif

/*------------- CDC --------------*/
/* 3 路：CDC0 = UART0 数据，CDC1 = UART1 数据，CDC2 = 控制台
   每路占 3 个端点（Bulk IN / Bulk OUT / Int IN），共 9 个，RP2040 上限 15 */
#define CFG_TUD_ENDPOINT0_SIZE   64
#define CFG_TUD_CDC               3
#define CFG_TUD_CDC_RX_BUFSIZE    1024
#define CFG_TUD_CDC_TX_BUFSIZE    1024
#define CFG_TUD_CDC_EP_BUFSIZE    64
#define CFG_TUD_CDC_NOTIFY        0

/*------------- Vendor (CMSIS-DAP v2) --------------*/
#define CFG_TUD_VENDOR            1
#define CFG_TUD_VENDOR_RX_BUFSIZE 512
#define CFG_TUD_VENDOR_TX_BUFSIZE 512
#define CFG_TUD_VENDOR_EP_BUFSIZE 64

/*------------- 其余 class 全关，省 RAM --------------*/
#define CFG_TUD_MSC               0
#define CFG_TUD_HID               0
#define CFG_TUD_MIDI              0
#define CFG_TUD_DFU               0
#define CFG_TUD_DFU_RUNTIME       0
#define CFG_TUD_NET               0
#define CFG_TUD_BTH               0
#define CFG_TUD_ECM_RNDIS         0
#define CFG_TUD_AUDIO             0
#define CFG_TUD_VIDEO             0
#define CFG_TUD_USBTMC            0

#ifdef __cplusplus
}
#endif

#endif /* _TUSB_CONFIG_H_ */

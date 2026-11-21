/*
 * cmsis_dap.h - CMSIS-DAP v2 (SWD) 调试接口
 *
 * 目标板接这三根线：
 *   GP14 -> SWCLK
 *   GP15 <-> SWDIO
 *   GP13 -> nRESET（可选，低有效，开漏上电）
 *   GND   -> GND（必须共地）
 *
 * 上位机：pyOCD / OpenOCD 0.12+ 直接识别为 CMSIS-DAP v2（WinUSB Bulk）。
 */
#ifndef CMSIS_DAP_H
#define CMSIS_DAP_H

#include <stdbool.h>
#include <stdint.h>

/* SWD 引脚（避开 UART0 GP0/1、UART1 GP8/9、PWR/IO0/EN GP4/2/3、LED GP25） */
#define SWD_PIN_SWCLK   14
#define SWD_PIN_SWDIO   15
#define SWD_PIN_NRESET  13

/* 引脚初始化（此三脚不再归控制口管） */
void swd_init_pins(void);

/* 连接目标：线复位 + JTAG-to-SWD + 读 DPIDR。成功返回 true */
bool swd_connect(void);

/* 断开：释放 SWDIO 为输入，nRESET 拉高 */
void swd_disconnect(void);

/* 设置 SWD 时钟（kHz，100..4000） */
void swd_set_clock_khz(uint32_t khz);

/* 目标复位脉冲（nRESET 低 20ms 再释放） */
void swd_target_reset(void);

/* 处理一帧 CMSIS-DAP 命令，返回响应字节数 */
uint32_t dap_process(const uint8_t *req, uint32_t req_len,
                     uint8_t *resp, uint32_t resp_max);

#endif /* CMSIS_DAP_H */

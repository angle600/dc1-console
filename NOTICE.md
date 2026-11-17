# NOTICE · 第三方资源与致谢

本项目的**代码**（`cli/`、`gui/`、`firmware/`、`tools/`）以 MIT 许可发布，见 `LICENSE`。

## 硬件资料（`hardware/`）

以下内容**不适用 MIT**，版权归原作者/原厂所有，本项目仅作个人研究与维修用途引用：

| 内容 | 来源 |
|---|---|
| 主控板正/背面标注图、芯片特写、拆机照、原厂固件日志、TTL 接线照 | [Samuel-0-0/phicomm_dc1-esphome](https://github.com/Samuel-0-0/phicomm_dc1-esphome)（社区反推资料） |
| `CAT9554.PDF`、`pcf8574.pdf`、`CSE7766` 手册、`CSE7766典型电路.png`、`永能 YX201.pdf` | 各芯片原厂（TI / NXP / 芯海 / 永能）公开 datasheet |
| `CSM64F02 WiFi模组用户手册V1.3.pdf`、`esp-wroom-02技术规格书.pdf` | 芯海科技 / 乐鑫科技 |

## 社区项目与个人致谢

- **killadm**：原厂固件导出、U7 抓包、模组对比图
- **老妖（SchumyHao）**：U7 驱动与逻辑分析
- **实验幼儿园小二班扛把子**：引脚走向
- **Heller / 巴山耗子**：早期资料整理
- **风中的 summer**：清晰拆机照
- **OttoWinter / ESPHome**：ESP8266 生态与 OTA 机制参考
- **MicroPython**：1MB Flash 变体与 `machine`/`network` API

## 免责

设备涉及 220V 强电，拆机、焊接、刷机均有触电与损坏风险；本项目不提供任何担保，
按 `docs/01-刷机方案.md` 的风险表自行评估。原厂固件请务必先 dump 备份。

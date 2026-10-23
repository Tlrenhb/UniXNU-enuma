# package/（骨架）

在 sheng 仓库里，`package/` 是板级探针与驱动源码的所在地（`TouchProbe.cpp`、`ShengGmu*`、`BootLog.c`、
`unixnu.toml` …）。enuma 的对应物应**按阶段逐步加入**，每个文件都要注明来源与实机依据：

| 阶段 | 计划加入 | 来源 |
| --- | --- | --- |
| P3 | `BootLog` 式日志窗口（无 UART 时取日志） | 移植 sheng 的 `package/BootLog.c` + `scripts/read-sheng-log.py` |
| P4 | UFS 板级常量头（GCC/PHY/ICE） | 新写，值来自 `sm8250.dtsi` + stock DT |
| P4 | 显示探针（SM8250 DPU） | 移植 sheng 的 `ShengDpuObservation.c` / legacy display 路径 |
| P4 | 触摸板级常量头 | 移植 sheng `TouchBoard.h`（先确认控制器型号：getevent/dmesg） |
| P6 | GPU 探针（Adreno 650） | 移植 `collect-msm-readonly.py`（改成 `xiaomi,enuma` 并重新固定查询 ID） |

不要一次性把 sheng 的 `package/` 全拷过来：其中约 60 个文件是 Adreno 740/Metal 专属，enuma 是 Adreno 650。

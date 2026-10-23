# 上机测量记录（enuma，2026-10-23）

来源：机主用 `tools/enuma-probe.sh` 在 Android 模式下采集（`summary.json`）。

| 项 | 实测值 | 用途 |
|---|---|---|
| `ro.product.device` | `enuma` | 机型确认 ✓ |
| `ro.product.model` | `M2105K81C` | 记录（小米平板 5 Pro 5G 的型号串；与 `enuma` 一致） |
| 面板 | `Physical size: 1600x2560` | 与厂商树 k81 双 CPhY 面板一致（1600×2560） |
| 触摸 | Novatek `NVT-ts`，`novatek_nt36523_k81_fw01.bin`，`pen-support=1`，irq-gpio 解析为 tlmm 39 | 与 `enuma-sm8250.dtsi` 完全一致 ✓ |
| `/proc/meminfo` | `MemTotal: 7793540 kB`（≈7.43 GiB） | 8 GiB 机型 |
| System RAM banks | `80894000-808fffff`、`85e00000-861fffff`、`8681c000-868fffff`、`92700000-b03fffff`、`b1400000-bbafffff`、`c0000000-ffbfffff`、**`ffc20000-27fffffff`** | 最高 bank 末尾 `0x280000000` → **`dram_size = 0x200000000`**（已填入 board.toml） |
| stock DT 根 compatible | `qcom,kona-mtp qcom,kona qcom,mtp` | **注意**：stock DT 不带 `xiaomi,enuma`（那是 dtbo/ABL 层的事）；我们 ADT 里的 `root-compatible = xiaomi,enuma\0qcom,sm8250\0` 是**自建的板身份**，与 pipa 的做法同构 |
| userdata 块大小 | 4096 B | ✓ |
| userdata 容量 | 242,455,916,544 B（59,193,339 × 4096） | 与 sysfs `size` 完全自洽 |
| userdata LBA | `first_lba = 0x290000`、`last_lba = 0x3b037fa`（sysfs start 21495808 / size 473546712，512B 扇区 ÷8）；59,193,339 × 4096 = 242,455,916,544 B **与 blockdev 一致** | 已填入 `[userdata]`，`check-enuma.py` = **0 outstanding measurement** |
| `fastboot getvar` | 未采集（设备当时在 Android 模式） | 需要时 `--fastboot-only` |

## 教训（已修进脚本）

- **`fastboot getvar` 在没有 fastboot 设备时会永久等待**（fastboot 自己不超时）→ 现在所有 adb/fastboot 调用都套 `timeout`，且只在检测到 fastboot 设备时才 `getvar`。
- **多数 Android 上没有 `sgdisk`** → 现在回退到 `/sys/class/block/<part>/{start,size}`（512 字节扇区 ÷8 得 4096 字节 LBA），并与 `blockdev --getsize64` 交叉校验。

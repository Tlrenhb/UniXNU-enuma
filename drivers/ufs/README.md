# drivers/ufs —— enuma 的 UFS 驱动（vendored）

本目录是 [UniXNU/ShengUfs](https://github.com/UniXNU/ShengUfs) @ `e2b32cc429e8cf1501161c1d8c517f8994ceb9b2`
的原样拷贝，加上下面这几处**为 enuma/SM8250 的改动**（其余一律不动，便于以后与上游对比）：

| 文件 | 改动 | 原因 |
|---|---|---|
| `GccUfs.h` | 整个文件重写 | SM8550 → SM8250：分支门偏移来自 `gcc-kona.c`，UFS/ICE/PHY 窗口与 IRQ 来自 `kona.dtsi:3069` 与合并后的 enuma DT；`ufs_phy_gdsc` 在 kona 是 `0x177004`，不在 `0x77000` 分支页里 |
| `NativeUfs.cpp` | 匹配串加入 `qcom,ufshc` | kona DT 声明的 compatible 就是 `qcom,ufshc`（`kona.dtsi:3070`），不改就只能匹配 sheng 的名字 |
| `NativeUfs.cpp` | GDSC 改用 `GCC_UFS_PHY_GDSCR_WINDOW/_OFFSET` 单独映射 | 同上：kona 的 GDSC 不在分支页内，原来的 `- 0x77000` 会读到窗口外 |
| `NativeUfs.cpp` | `read()` 里的计时用 `NATIVE_VERIFIED_BLOCK` 包起来 | 上游把 `timerTicks`/`native_ufs_read_ticks` 声明在 verified-block 块里，却无条件使用；sheng 始终打开该宏所以看不出来，我们第一版不打开就编译失败 |

构建方式：`scripts/build-arm-driver.py NativeUfs` → `build/sheng-ufs/libShengUfs.a`
（`build-xnu-4k-baseline.sh` 在 `XNU_NATIVE_UFS=1` 时 force-load 这个路径）。

**当前阶段只读**：`native-ufs-probe=2` 只读 GCC 状态、UFS PHY GDSC、UFSHCI 寄存器，然后 panic 让 panic 屏打印，不写任何存储。

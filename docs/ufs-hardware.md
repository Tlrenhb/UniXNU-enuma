# enuma 的 UFS 硬件事实（移植存储驱动用）

全部来自一手材料，逐条给 `文件:行`：

- 设备树：`out/astide/enuma.dtb`（我们构建时用的那份，sha256 `296c7e91…`）
- 内核源码（用户指定的信源）：`LineageOS/android_kernel_xiaomi_sm8250`
  - `arch/arm64/boot/dts/vendor/qcom/kona.dtsi:3069`（`ufshc_mem` 节点）
  - `drivers/clk/qcom/gcc-kona.c:3569` 起（UFS 各分支时钟）
- 参考实现：`UniXNU/ShengUfs` @ `e2b32cc`（SM8550，sheng 实机验证）

## 地址窗口

| 资源 | 值 | 来源 |
|---|---|---|
| UFS 主机控制器 | `0x01d84000`，长度 `0x3000`，名字 `ufs_mem` | enuma.dts `ufshc@1d84000` 的 `reg` |
| UFS ICE | `0x01d90000`，长度 `0x8000`，名字 `ufs_ice` | 同上 |
| UFS PHY（QMP v4） | `0x01d87000`，长度 `0xe00`，名字 `phy_mem`；`compatible = "qcom,ufs-phy-qmp-v4"` | enuma.dts `ufsphy_mem@1d87000` |
| 中断 | `GIC_SPI 265`（0x109），LEVEL_HIGH | 两处 DT 一致 |
| 每方向 lane | 2 | DT `lanes-per-direction` |
| UFS PHY GDSC | `0x0177004`（`ufs_phy_gdsc`，`qcom,gdsc`） | enuma.dts `qcom,gdsc@177004` |

## GCC 里 UFS 用到的分支（kona）

`GCC_BASE = 0x00100000`，偏移来自 `gcc-kona.c`，`enable_mask = BIT(0)`（`*_hw_ctl_clk` 是 `BIT(1)`）：

| 时钟 | 寄存器 |
|---|---|
| `gcc_ufs_phy_axi_clk` | `0x77010` |
| `gcc_ufs_phy_ahb_clk` | `0x77018` |
| `gcc_ufs_phy_tx_symbol_0_clk` | `0x7701c` |
| `gcc_ufs_phy_rx_symbol_0_clk` | `0x77020` |
| `gcc_ufs_phy_rx_symbol_1_clk` | `0x770b8` |
| `gcc_ufs_phy_unipro_core_clk` | `0x7705c` |
| `gcc_ufs_phy_ice_core_clk` | `0x77064` |
| `gcc_ufs_phy_phy_aux_clk` | `0x7709c` |
| `gcc_aggre_ufs_phy_axi_clk` | `0x770cc` |
| `gcc_ufs_1x_clkref_en` | `0x8c000` |

DT 里 `freq-table-hz` 给出 core/unipro/ice 的 `37.5 MHz … 300 MHz`（`0x23c3460..0x11e1a300`）。
PHY 的 `ref_aux_clk` 是 `gcc 0xad`，`ref_clk_src` 是 `rpmhcc 0x00`（19.2 MHz）。

## 关键结论

1. **ShengUfs 不写 GCC，也不初始化 PHY。** `NativeUfs.cpp` 里 9 处 `GCC_*` 全是 `read32()` 诊断，
   `GccUfs.h` 的注释也写着 "No write is performed by NativeUfs yet"。它假设 **引导程序（ABL）已经把
   UFS 控制器和 PHY 初始化好** —— enuma 的 ABL 本来就从 UFS 读 `boot.img`，所以这个前提成立。
2. **kona.dtsi 的 `ufshc_mem` 节点没有 `iommus` 属性**（enuma 的合并 DT 也没有），
   说明 UFS 的 SMMU 流很可能是 **bypass** 状态。sheng 用的是 `smmuBypassStream(0x60, …)`（它的 SID），
   kona 的正确 SID 尚未在设备树上找到 —— 这一项要靠上机读寄存器/试读来定，不能猜。
3. 因此 enuma 的存储移植顺序是：先只读寄存器（`HCS`/`HCM`/版本 + GCC 那几支的开关状态）→
   再发一条 NOP/READ UPIU → 最后读 userdata 的前几个扇区。

## 还没定的

- UFS 的 SMMU 流 ID（DT 里没有，需要上机探测或从 abl 的 SMMU 配置里读）。
- ICE 是否对这个 LUN 使能（若使能，读回的是密文；APFS 容器的可读性取决于此）。
- PHY/时钟是否真的被 ABL 留在开启状态（第 2 条的第一步就会回答）。

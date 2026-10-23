# UniXNU-enuma（pre-dump 完成态）

> **当前状态：pre-dump 完成态（task-16，2026-10-23）。** 目录结构与 `UniXNU-sheng` / `UniXNU-pipa` 同构；
> **凡能从一手公开材料确定的数值都已填入并在注释里给出 `文件:行` 来源**，只剩两项真正必须上机测量的数值是
> `TODO(enuma)`：`[userdata] first_lba/last_lba` 与 `[payload] dram_size`。
> `python3 scripts/check-enuma.py` 现在给出 **0 error / 2 TODO**（退出码 0）。
> 这个仓库仍**不能直接构建出可烧写产物**——它缺的是 UEFI 侧（`enumaPkg`）与 pin 住的构建树，不是数值。

设备：小米平板 5 Pro 5G（codename `enuma`，SoC **SM8250-AC / 骁龙 870**，Kona）。
参考实现：[UniXNU-sheng](https://github.com/UniXNU/UniXNU-sheng)（唯一完整样例，SM8550）、
[UniXNU-pipa](https://github.com/xVanTuring/UniXNU-pipa)（小米平板 6，**同 SoC**，无实机准备的最小仓库）、
[reAAAq/UniXNU-umi](https://github.com/reAAAq/UniXNU-umi)（小米 10，**同 SoC 且真机跑通**：UEFI 菜单→UFS 根卷→launchd→持久 Data）、
[ht94911/UniXNU-cas](https://github.com/ht94911/UniXNU-cas)（小米 10 Ultra，SM8250，UEFI/菜单真机验证）。
数值取证笔记：[../astide-notes.md](../astide-notes.md)；同 SoC 端口经验：[umi-cas-learnings.md](umi-cas-learnings.md)；
路线图：[../porting-plan.md](../porting-plan.md)。

---

## 1. 为什么 enuma 的起点比 sheng 好

- **enuma 与 pipa 是同一颗 SoC**（SM8250-AC / 骁龙 870）。GIC 地址、timebase、核心编号、DRAM base 等 SoC 级常量
  引用 pipa 从公开 `sm8250.dtsi` 摘出的值，不需要逆向。
- pipa 证明了**最小可开工的板级仓库只有 20 个文件**，它的 UEFI 适配只在 `uefi-sheng` 之上改了 4 个文件
  （`pipa.dsc` / `pipa.fdf` / `Include/DXE.inc` / `Resources/Configs/pipa.toml`），并复用了 shengPkg 的
  `ShengBootMemoryDxe`（从设备树取范围，跨板通用）。
- **私钥材料已不再是阻塞**：`UniXNU/scripts/build-original-system-trust.py`、`make-owner-trust-bundle.py`、
  `prepare-owner-data-fixture.py`、`build-owner-os-policy.py` 都在公开仓库里；平台种子本地生成；
  Mac-only 步骤可跑在 GitHub macOS runner 上。
- **同 SoC 已有真机证明**：umi 在 SM8250 上跑到了 Hello/Setup Assistant + 持久 Data（`docs/validation.json`：
  `setup_ui` 由机主确认、`full_boot_readback_verified=true`、8 核在线），
  **且它把 SM8250 可用的 boot image 布局（header_version=1/4096/0x8000/0x1e00000）与 8 核 cpu_regs 直接公开了** ——
  这两项已被我们采用。
- **enuma 的一手材料比预想的多**（`../astide-notes.md` 全篇）：
  - 内核/设备树：`AstideLabs/android_kernel_xiaomi_sm8250`（pin `4ae1f1e4c89c`）+ 上游 LineageOS 树；
    `enuma-sm8250.dtsi`、`enuma-pinctrl.dtsi`、`enuma-sm8250-overlay.dts`（board-id `<46 0>`，elish 是 `<47 0>`）。
  - 触摸：**Novatek NT36523**（`CONFIG_TOUCHSCREEN_NT36523_HOSTDL_SPI=y`），SPI `qupv3_se4_spi`、CS 0、
    19.2 MHz、IRQ `tlmm 39`、无 reset GPIO、`swrst 0x03F0FE`/`rd-fast 0x03F310`、
    固件名 `novatek_nt36523_k81_fw01.bin`（回退 `novatek_ts_fw_k81.bin`）。
  - 面板：k81 双 **CPhY**（`dsi_k81_42_02_0a` 默认 / `dsi_k81_35_02_0b`），每 DSI 800×2560、120 fps、3 lane、
    供电 vddio 1.88 V / enp 5.5 V / enn 5.5 V；背光 KTZ8866（I2C 0x11，panelid GPIO 38）。
  - UFS：`ufshc@0x1d84000`（ICE 0x1d90000、PHY 0x1d87000）、**IRQ SPI 265**、2 lane、ref 19.2 MHz、
    PHY = **QMP v4**；splash 保留区 **0x9c000000 + 0x2300000**。
  - 官方固件包（hyperos.fans 等有 `elish`/`enuma` 机型页）：用于取 `dtbo`/`dtb`，以及
    `xbl`/`uefi` 镜像 → 用 UEFIReader 提取厂商 DXE 驱动（`Binaries/<device>/` 的来源）。

## 2. 可以离线完成 vs 必须上机

| 工作 | 离线（无平板） | 必须上机 |
| --- | --- | --- |
| 环境准备、克隆仓库、跑 QEMU 参考 | ✅ | |
| UEFI 平台包（`enumaPkg` 的 DSC/FDF/ACPI）骨架 | ✅（照 pipaPkg 改） | 需实机验证 |
| `board.toml` 数值（splash 0x9c000000、面板几何、触摸 SPI/IRQ、board-id/msm-id、bootimg 布局、6 个 IPSW pin、8 核 cpu_regs） | ✅ **本次已填** | |
| `[kernel] archives` / 四阶段 defconfig / preflight 脚本 | ✅ **本次已填** | |
| `[userdata] first/last_lba`、`[payload] dram_size`、实际面板变体、UFS 实测 gear、`fastboot getvar` | ❌ | ✅ `sgdisk -p`、`/proc/iomem`、dtbo、getevent… |
| 内核/owner 链/payload 的通用部分 | ✅（可在 QEMU 上验证） | |
| `fastboot boot` 迭代固件 | ❌ | ✅ |
| userdata 刷写、首次启动、桌面 | ❌ | ✅ |
| UFS / 显示 / 触摸 / GPU 驱动 | 只能写代码与静态检查 | ✅ 全部验收 |

## 3. 分阶段 defconfig（`configs/`，四份**累积**配置）

| 文件 | 阶段 / 进入条件 | 期望验收（官方 bring-up 阶段，`docs/zh/porting/bring-up.md`） |
| --- | --- | --- |
| `enuma_defconfig` | **阶段 1 内核入口**：无前置条件 | stock ABL `fastboot boot` 成功、framebuffer/串口有输出、UniXNU 菜单出现（`UNIXNU:`）、DarwinBoot 打印地址后退出 boot services、内核入口探针/panic 屏可见（**无 root 卷**） |
| `enuma_ufs_defconfig` | **阶段 2 UFS 根卷**：阶段 1 实机通过 + NativeUfs 的 SM8250 寄存器改写完成 | 块设备/负载卷可见、UFS 控制器与 PHY 初始化日志、APFS 卷挂载；`+NATIVE_UFS=y`、`+SMMU4K_PROBE=y` |
| `enuma_display_touch_defconfig` | **阶段 3 显示+触摸**：阶段 2 通过 + 选定"继承 ABL 帧缓冲"路线 | 面板保持点亮、菜单可点、内核控制台可读；触摸出现输入设备与**物理触点坐标**（区分"HID 挂载"与"物理触点"，umi 的判据）；`+NATIVE_LEGACY_DISPLAY=y`，触摸开关待核对真名 |
| `enuma_owner_defconfig` | **阶段 4 owner 链**：阶段 3 通过 + 公开脚本生成 owner 材料 + userdata LBA 实测 | `VERIFIED_ROOT_PROBE` 通过、Data 卷挂载且**重启后仍在**、launchd 起来、Setup Assistant 出现、BOOT_PROGRESS 有输出；`+VERIFIED_ROOT_PROBE/OWNER_*/BOOT_PROGRESS/SOURCE_MOCKFS/NATIVE_RAM_BLOCK` |

`scripts/check-enuma.py` 会强制：四份文件都存在且**逐级包含**、阶段 1 不得出现 SM8550/后续阶段开关、
阶段 4 必须包含整条 owner 链、每一阶段都必须带最小集（11 个通用开关）。

## 4. 与 umi 的对照：**umi 有、我们不能直接照抄**的项

（完整版见 [umi-cas-learnings.md](umi-cas-learnings.md) §2；这里是红线摘要）

| umi 的做法 | enuma 为什么不能抄 |
| --- | --- |
| 触摸 = ST FTS（`fts_521`/`ftsCore`）、native GPI DMA I²C、先拉 PM8150B GPIO5 常开轨 | enuma 是 **Novatek NT36523 / SPI / 19.2 MHz / IRQ tlmm 39 / 无 reset GPIO / 无该 PMIC 轨**；变体判定靠面板 vendor info `lockdown[1]` |
| `[userdata] first_lba=0x2c0000 / last_lba=0x3b51ffa` | 分区表逐机逐容量实测（umi 自己写了 "Do not copy sheng's partition LBAs"）；我们保持 `0x0`+TODO |
| `dram_size = "0x2f9900000"`（12 GiB 机型的 3 段 bank 之和） | enuma 是 6/8 GiB 版本，bank 未知 → TODO |
| `ENABLE_SECUREBOOT="0"`、`DEVICE_MODEL="0"` | 固件签名策略选择，不是硬件事实；我们保持 sheng 的 `"1"`，但记录"卡校验时可降到 0"的先例 |
| `umi_defconfig` 把功能开关全 `=n`，功能靠 `build_umi_xnu.py --profile phone` 逐级加 | 我们走**四个累积 defconfig 文件**（更显式、可离线评审）；不要把 umi 的 `=n` 误读成"功能不存在" |
| `CRYPTO_BOOTSTRAP="source"`（最小基线） | 我们与 pipa/sheng 一致用 `"prelinked"` |
| 大量本地生成记录（ABI 元数据、参考 Mach-O、Data seed、XBL import manifest）不进 git | umi 自称 "Full reproduction is not yet a single clean-clone command"；我们也要如实列出"必须自备"的输入 |
| `Mac-Bash3.patch` / `Dxe-Trace.patch` / `Display-Services-Trace.patch` | 前者是宿主 macOS 兼容，后两者是调试探针；核心 6 条清单（Mu-Silicium 上游 5 + Gic-Spurious）已单独列出 |
| **`source-lock.json`：四个 checkout 钉 SHA，且警告 "Do not substitute current upstream main"** | 这条**必须学**：current UniXNU main 的 686 个路径里已无 `build-arm-driver.py`/`build-link-stubs.py`/`build-platform-power.py`（本次 API 核实），它们现在由 UniXNU-sheng 提供 |

## 5. 目录

| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `board.toml` | **pre-dump 完成态** | 已填：6 个 IPSW pin、`[kernel] archives`（9 条，pipa 模板 + pin 告警）、`[bootimg.image]`（umi 实机值）、`[[bootimg.dtb_node]]` splash（0x9c000000+0x2300000）、`[payload.adt]`（8 核 cpu_regs、psci smc）、`timeout=-1`、六补丁清单注释。剩 2 个 `TODO(enuma)` |
| `configs/enuma_defconfig` | **阶段 1** | 最小首启集（11 个通用开关 + 两个 `=n`），带进入条件/验收注释 |
| `configs/enuma_ufs_defconfig` | **阶段 2** | +`NATIVE_UFS`、+`SMMU4K_PROBE`，文件头写明 SM8250 的 UFS 全部常量来源 |
| `configs/enuma_display_touch_defconfig` | **阶段 3** | +`NATIVE_LEGACY_DISPLAY`；触摸开关真名待核对（文件内有核对命令） |
| `configs/enuma_owner_defconfig` | **阶段 4** | +owner 链 9 项 + `SOURCE_MOCKFS`/`NATIVE_RAM_BLOCK` + Data 卷 UUID 占位 |
| `scripts/make-enuma-adt.py` | 骨架但可运行 | SoC 常量已填（来源 `sm8250.dtsi`，经 pipa 验证）；板级值走**必填命令行参数**，不填不生成 |
| `scripts/check-enuma.py` | **已扩展（含期望集合门）** | 校验：占位符、IPSW pin 数量、`[kernel] archives` **应有 9 条一条不缺**（`ARCHIVES_REQUIRED`；缺任一条报 `Missing archive: <name> (no builder makes it, supply the archive)`）+ `--tree` 逐条解析、四阶段累积性、preflight 路径、六补丁清单（`--uefi`）、bootimg header_version/pagesize、splash 区域、ADT 断言（psci/cpu_regs/owned_memory）。自测：删除 0/1/4 条 → exit 0/1/1 |
| `scripts/build-enuma-uefi.sh` | 骨架 | 固件迭代回路（参照 pipa 的 `uefi-round.sh`）；P1 时应加 pin 守卫 |
| `scripts/fetch-enuma-dts.py` | **可用（双来源，已实跑验证）** | `--source {astide,lineageos}`（默认 astide，pin `4ae1f1e4c89c`）→ 抓闭包 → 编译 kona-v2.1 基座 + enuma overlay → 合并 `out/<source>/enuma.dtb` |
| `umi-cas-learnings.md` | **新增** | 同 SoC 真机端口 umi/cas 的可复用做法、不可照抄项、pin 告警的独立核实、cas 的 EFI-only 工具清单 |
| `package/` `patches/` `kits/` `drivers/` | 占位 | 前期不需要 |

## 5b. `[kernel] archives` 的 9 条构建器：谁提供、从哪里取（task-18 ③）

**没有任何单一仓库能提供全部 9 条。** 下表是 2026-10-23 逐仓 API 核实的结果（"—" = 该仓没有这个文件名）：

| builder | UniXNU main | UniXNU-sheng | UniXNU-umi | UniXNU-pipa | 我们从哪里取 |
| --- | --- | --- | --- | --- | --- |
| `build-corecrypto-14.py` | ✅ | — | — | — | main |
| `build-corecrypto-14.py --namespace` | ✅ | — | — | — | main |
| `build-arm-driver.py` | — | ✅ | ✅（名为 `build-umi-arm-driver.py`） | ✅ | pipa（条目名一致） |
| `build-native-aes-bindings.py` | ✅ | — | — | — | main |
| `build-libpthread-kernel.py` | ✅ | — | — | — | main |
| `build-link-stubs.py` | — | — | — | ✅ | **只有 pipa** → 必须 vendored |
| `build-platform-power.py` | — | —（sheng 名为 `build-sheng-platform-power.py`） | —（umi 名为 `build-umi-platform-power.py`） | ✅ | **只有 pipa** → 必须 vendored |

**按 pin 解析的具体步骤**（官方 resolution order：`docs/zh/porting/device-repository.md:23,273`）：

1. 写 `source-lock.json`（照 umi 的格式：UniXNU / uefi-enuma / Mu-Silicium base / UniXNU-sheng 四个 SHA）
   并用 `unixnu.lock` 固定依赖源；**不要用最新 upstream main**（main 的 686 个路径里已无
   `build-arm-driver.py`/`build-link-stubs.py`/`build-platform-power.py`）。
2. 建树：`./unixnu defconfig enuma_defconfig -D ../UniXNU-enuma` → 设备仓库同名模块以符号链接进入
   `out/enuma/tree/`，**设备仓库覆盖基础仓库**；`unixnu status` 标记本地模块与未固定锁文件。
3. 逐条解析顺序：`out/enuma/tree/scripts/<builder>`（设备仓库覆盖层）→ UniXNU `scripts/<builder>` →
   该板用到的驱动/feeds 仓库 `scripts/<builder>`。
4. 把只存在于 pipa 的两条（`build-link-stubs.py`、`build-platform-power.py`）**复制进本仓库 `scripts/`**
   （保留出处与许可），条目名不变即可被第 3 步第一层解析。
5. 验证：`python3 scripts/check-enuma.py --tree out/enuma/tree` 必须 0 error（同时检查"能解析"与"9 条不缺"）。

## 6. 开工顺序（最小路径）

```sh
# 0. 环境（Mac 或 GitHub macOS runner；umi 记录 Linux 未 qualify）
brew install llvm lld cmake ninja mtools dtc qemu

# 1. 克隆并**按 pin** 建树（不要用最新 main：见 umi-cas-learnings.md §3）
git clone --recurse-submodules https://github.com/UniXNU/UniXNU
git clone https://github.com/xVanTuring/UniXNU-pipa        # 同 SoC 参照
git clone https://github.com/xVanTuring/UniXNU-qemu        # 无硬件参照
git clone https://github.com/reAAAq/UniXNU-umi             # 同 SoC 真机参照（只读）
git clone https://github.com/ht94911/UniXNU-cas            # EFI-only 参照（只读）
# 本骨架改名后放在 UniXNU 旁边：../UniXNU-enuma
# UEFI：复制 uefi-pipa 为 uefi-enuma，再改 enumaPkg

# 2. 离线自检（不需要平板，先跑这一步）
python3 scripts/check-enuma.py                      # 期望：0 error / 2 TODO
python3 scripts/check-enuma.py --tree <build-tree>  # 期望：9 条 archives 全部能在树里找到构建器
python3 scripts/check-enuma.py --uefi ../uefi-enuma # enumaPkg + 六补丁清单

# 3. 离线：先让通用部分在 QEMU 上跑通
cd UniXNU && ./unixnu defconfig qemu_defconfig -D ../UniXNU-qemu
./unixnu build package kernel -D ../UniXNU-qemu
python3 out/qemu/tree/scripts/run-qemu.py --display cocoa

# 4. 上机测量（把两个 TODO 填掉），然后按阶段推进
#    adb shell su -c 'sgdisk -p /dev/block/sda'  → [userdata]
#    adb shell su -c 'cat /proc/iomem'           → [payload] dram_size
./unixnu defconfig enuma_defconfig -D ../UniXNU-enuma          # 阶段 1
./unixnu fetch -D ../UniXNU-enuma
./unixnu ipsw fetch -D ../UniXNU-enuma
./unixnu build kernel -D ../UniXNU-enuma
./unixnu build package -D ../UniXNU-enuma
UNIXNU_UEFI=../uefi-enuma ./unixnu build firmware -D ../UniXNU-enuma
./unixnu build payload -D ../UniXNU-enuma \
  --unique-chip-id "$(cat .cache/platform-seed)" --trust-cache <bundle> --data-ramdisk <seed>
./unixnu bootimg -D ../UniXNU-enuma --uefi ../uefi-enuma --dtb <enuma.dtb>
# 阶段 2/3/4：把 board.toml 的 [kernel] defconfig 换成对应文件（或 --defconfig），
#            并把该阶段新开的归档构建器加进 [kernel] archives。
```

## 7. 红线（不要做）

- **不要**把 sheng 的 `NATIVE_UFS` / `NATIVE_ADRENO` / `OWNER_*` 配置直接打开：UFS 驱动是 SM8550 寄存器、
  GPU 套件是 Adreno 740、owner 链需要 owner 签名策略。阶段 1 全部关闭（`enuma_defconfig` 已如此）。
- **不要**照抄 sheng 的 `sheng-*` 内核参数（除 `sheng-pmgr=2`，它不访问硬件）；`armcpu-spi-ipi` 是 SM8550 特性。
- **不要**照抄 umi 的触摸路径（ST FTS / PM8150B GPIO5）到 Novatek SPI 上。
- **不要**用最新 upstream `main` 替代 pin（umi 的告警，我们已独立核实）。
- **不要**在没有备份的情况下 `fastboot flash userdata`：它会抹掉 Android 数据。
- **不要**把 GPU 放进第一个里程碑：umi（同 SoC）的 `validation.json` 记录 `gpu_acceleration=false`。

## 8. 许可

BSD-2-Clause-Patent（与 UniXNU / UniXNU-sheng 一致）。骨架由 task-7 生成，task-16 升级为 pre-dump 完成态；
数值来源见 `../astide-notes.md`、`../uefi-prereq.md` 与本仓库内注释。
# 从两个**硬件验证过的 SM8250 端口**学到什么（umi / cas）

> task-16 交付物之一。两个参照仓库都以**只读**方式读取（raw.githubusercontent + GitHub contents API），
> 读取日期 2026-10-23，读取时的 HEAD：
> - `reAAAq/UniXNU-umi`（小米 10 / umi / SM8250），默认分支 `main`，pushed `2026-10-23T06:53:52Z`，
>   description: *"Xiaomi Mi 10 (umi / SM8250) UniXNU port: iPadOS 17.7.11 Hello, UFS, persistent Data, CPU display; touch in progress"*。
> - `ht94911/UniXNU-cas`（小米 10 Ultra / cas / SM8250），默认分支 `main`，pushed `2026-10-22T20:14:08Z`，
>   description: *"...hardware-verified UEFI/UniXNU, RAM FAT and EFI return. XNU not yet tested."*

---

## 1. umi 给我们的**可直接复用**的东西

| # | 做法 / 值 | umi 出处 | 我们怎么用 |
|---|---|---|---|
| 1 | `[bootimg.image]` 的 **header_version = 1 / pagesize 4096 / base 0 / kernel_offset 0x8000 / tags_offset 0x1e00000** —— 这是**在 SM8250 真机上被 stock ABL 接受过**的一组值（与 `pipa.toml` 只给 header_version=1 互为印证） | `umi/board.toml` `[bootimg.image]` | 已写进 `board.toml`；并在 check 脚本里对 header_version∈{0,1,2} 与 pagesize 2 的幂做校验 |
| 2 | **8 核 cpu_regs**：`"0,0x100,0x200,0x300,0x400,0x500,0x600,0x700"`，并且 umi 记录了 8 核在线的**每核 120 s 计时器读数** | `umi/board.toml` `[payload.adt].cpu_regs`；`umi/docs/validation.json`（`smp_attempt=50`、`logical_cpus_online=8`、`per_core_timer_at_120` 8 个值） | 已写进 `board.toml`；`make-enuma-adt.py` 的断言（≤8 项、首项为 0、0x100 对齐）与之一致 |
| 3 | `psci_method = "smc"` 显式写在 `[payload.adt]`（不是靠默认） | `umi/board.toml` | 已写进 `board.toml`，并加了 ADT 断言 |
| 4 | `[ipsw.sha256]` **pins 6 个成员**（kernelcache / DeviceTree / 两个 dmg / 两个 .trustcache），而不是只 pin 两个 dmg | `umi/board.toml` | 同一份 IPSW → 同样的哈希，已补进我们的 `board.toml`（6 条） |
| 5 | payload 菜单用 `timeout = -1`（停在菜单，不自动启动） | `umi/board.toml` `[payload].timeout` | 已采用：bring-up 阶段比自动启动好用 |
| 6 | **`source-lock.json`**：把 `UniXNU` / `uefi-umi` / `Mu_Basecore` / `UniXNU-sheng` 四个 checkout 钉到具体 commit | `umi/source-lock.json`（4 个 SHA） | 我们应照做（见 §3 告警）；本骨架的 `unixnu.lock` 机制相同但要等 `unixnu fetch` 生成 |
| 7 | **`docs/validation.json`**：机器可读的"到底验到了哪一步"，并把**否定项**也写进去（`gpu_acceleration=false`、多点触控/挂起恢复未验证、`full_boot_readback_verified=true`、`data_header_bitmap_unchanged_during_install=true`） | `umi/docs/validation.json`（schema 2，`date: 2026-10-23`） | 我们也应有 `docs/validation.json`：**"没验的写 false"，不要用 README 的形容词代替**。umi 把 `touch_working=true` 与 `hid_reports=0` 分开记，正是"物理触点 ≠ HID 挂载"的范例 |
| 8 | 把 **`bringup/setup-workspace.py`** 当作"建工作区 + 校验 pin + 链接 checkout"的入口，且**拒绝已存在的输出目录或 pin 不匹配** | `umi/docs/BUILD.md`；`umi/bringup/` | 我们的 `scripts/build-enuma-uefi.sh` 仍是骨架；P1 时应升级为同样的"pin 不匹配就拒绝"的守卫 |
| 9 | 固件路径用 `--display-profile inherited --input-profile buttons --payload-profile ram --loader-profile darwinboot` | `umi/docs/BUILD.md` | 对应我们阶段 3 的"继承 ABL 帧缓冲"路线（pipa 的 `USE_CUSTOM_DISPLAY_DRIVER=0`），即：**不自建显示初始化**先亮屏 |

## 2. umi 有、我们**不能照抄**的（重要）

| # | umi 的做法 | 为什么不能照抄到 enuma |
|---|---|---|
| 1 | **触摸 = ST FTS（`fts_521` / `ftsCore.c`），native GPI DMA I²C，且要先拉起 PM8150B GPIO5 常开轨再走 supply/reset 序列**（attempt 80 的修复：`umi-fts-gpi=1`，等 4000 µs 并回读） | enuma 是 **Novatek NT36523，SPI `qupv3_se4_spi`，19.2 MHz，IRQ `tlmm 39`，无 reset GPIO**（astide `enuma-sm8250.dtsi:286-327`）。总线不同、供电不同、变体判定不同（enuma 用面板 vendor info `lockdown[1]`，不是 PMIC 轨） |
| 2 | 触摸驱动来源是 MiCode 的 `umi-q-oss` `fts_lib`，并用哈希校验解析器 | 我们的驱动参考是 AstideLabs/LineageOS sm8250 树的 `nt36523/`（compatible `novatek,NVT-ts-spi`），**不是** `nt36xxx/`（后者 compatible 只有 `novatek,NVT-ts`，且不解析 fw-name） |
| 3 | `[userdata] first_lba=0x2c0000 / last_lba=0x3b51ffa`（256 GB 的 Mi 10） | **分区表是逐机逐容量实测**，umi 自己也写了 *"GPT geometry read and CRC-checked on this exact umi"*、"Do not copy sheng's partition LBAs"。我们保持 `0x0` + TODO |
| 4 | `dram_size = "0x2f9900000"`（12 GiB 机型的三段 FDT DRAM bank 之和） | enuma 有 6/8 GiB 版本，bank 划分未知 → 保持 TODO |
| 5 | `[firmware.defines] ENABLE_SECUREBOOT = "0"` + `DEVICE_MODEL = "0"` | 这是固件签名策略/模型串的选择，不是硬件事实。我们保持 sheng 的 `"1"`，但**记录 umi 的先例**：若 `fastboot boot` 卡在镜像校验，降到 0 是一种已被验证过的调试路径 |
| 6 | `umi_defconfig` 的最小基线把 `NATIVE_UFS / TRUST_CACHE_RUNTIME / CS_HASH_CACHE / SOURCE_MOCKFS` 全部显式 `=n`，真正的功能靠 `tools/build_umi_xnu.py --profile phone` 逐级加 | 我们的路线是**四个累积的 defconfig 文件**（阶段 1→4），更显式、更适合离线评审；两种都行，但**不要把 umi 的 `=n` 基线误读成"这些功能不存在"** |
| 7 | `CONFIG_XNU_CRYPTO_BOOTSTRAP="source"`（最小基线） | pipa/sheng 与我们用 `"prelinked"`；umi 在 phone profile 里才切到 prelinked。属"同一件事的两种编排"，不是对错 |
| 8 | 大量**本地生成记录**（`ipsw-inputs.json`、`out/umi/storage-abi/`、参考 Mach-O、APFS Data seed、身份 JSON、XBL import manifest）仍不进 git，`--reuse-storage-from 55` 是本地增量捷径 | umi 自己声明 *"Full reproduction is not yet a single clean-clone command"*。我们不要假装 enuma 能一键复现；把这类输入逐条列进"必须自备"清单 |
| 9 | payload 64 MiB / owned 256 MiB（基线）→ 124 MiB / `0x130000000`（root profile） | 我们 `size_mib = 192` 与 sheng/pipa 对齐；owned_memory_size 仍待实机 |
| 10 | `[firmware] patches` 含 `Mac-Bash3.patch`（macOS bash3 兼容）、`Dxe-Trace.patch`、`Display-Services-Trace.patch` | 前一个是**宿主环境**补丁（Linux 上无意义），后两个是调试探针。我们只保留 6 条核心清单（Mu-Silicium 上游 5 + Gic-Spurious） |

## 3. 最有价值的一条告警：**不要用最新 upstream `main` 替代 pin**

umi `docs/BUILD.md` 原文：

> "Use the commits in `UniXNU-umi/source-lock.json`. In each checkout, run `git checkout <the-corresponding-commit>` … **Do not substitute current upstream `main`: upstream has since split its drivers into repositories and changed several interfaces.**"

本次我们**独立核实了这条告警**（不是转述）：

- current `UniXNU/UniXNU@main` 的递归 git tree 共 686 个路径（API 返回 `truncated: false`），
  其中**不存在** `build-arm-driver.py`、`build-link-stubs.py`、`build-platform-power.py`、任何 `native-ufs` 构建器；
  只有 `patches/xnu/sheng-platform-power.patch` 残留。
- 这三个构建器**现在由设备/驱动树提供**：`UniXNU-sheng/scripts/` 里有 `build-arm-driver.py`、
  `build-native-legacy-display.py`、`build-native-mobile-display.py`、`build-boot-progress.py`、
  `build-smmu4k-probe.py`、`build-sheng-platform-power.py`（123 个文件，API 列表核实）。
- 我们 `board.toml` 的 `[kernel] archives` 抄的是 **pipa 的 9 条**（同 SoC、同一套最小 defconfig），
  它们在 pipa 的 pin 上可解析；在我们这边**必须**用
  `python3 scripts/check-enuma.py --tree <build-tree>` 逐条验证（脚本已实现；对空树会报 9 条 error，
  模拟内核步骤的 `no builder makes it, supply the archive`，源码位置 `scripts/unixnu/kernel.py:68`）。

**结论**：先把 `source-lock.json`（或 cas 的 `source-pins.json`）写出来，再谈构建。

## 4. cas 给我们的东西（EFI-only 阶段最有用）

cas 是**纯 UEFI 阶段**的端口（`board.toml` 只有 `[board]` + `[firmware]` + `[cas]`，没有 kernel/IPSW/userdata），
它的价值在"把固件侧的自检做成脚本"：

| 做法 | cas 出处 | 我们的用法 |
|---|---|---|
| 一份**过程化生成的 memory map**（`config/memory-map.json`，8.9 KB）+ 生成器 `scripts/generate_memory_map.py` + 校验器 `scripts/check_memory.py` | cas 仓库 `config/`、`scripts/`（API 列表核实） | 这正是我们 P1 要补的 `MemoryMapLib` 检查（当前 check-enuma.py 里标为未实现），SM8250 的**结构**可以直接参考，数值仍须 enuma 实机 |
| `scripts/prepare_boot_dtb.py` —— 专门准备 boot DTB（含保留区） | 同上 | 对应我们 `[[bootimg.dtb_node]]` 的 splash 节点；cas 的做法值得抄结构 |
| 显式的**构建守卫**：`cas_generic_build_guard.py` + `[cas].preflight` + `profile = "efi-only-ram"` | cas `board.toml` | 我们已有 `[firmware].preflight`；阶段化 profile 的思路与我们四个 defconfig 一致 |
| **`source-pins.json`**：不只 pin 顶层仓库，还 pin 每个 submodule（`Common/Mu`、`Mu_Basecore`、brotli、oniguruma、libfdt…）+ `source_date_epoch` + `candidate` 名 | cas `source-pins.json` | 比 umi 的 `source-lock.json` 更彻底；我们建 P1 工作区时应产这个格式（可复现构建还需要 `source_date_epoch`） |
| cas 明确写"通用 firmware/bootimg 路径不实现本板的 provenance/BootShim，请用 `DeviceBuild.py` + 本板 packer" | cas `board.toml` `[cas]` | 提醒：Mu-Silicium 的通用路径与板级 packer 是两条路，别混用 |

## 5. 落地到 enuma 的动作清单

1. **P1 第一步**：写 `source-lock.json`（UniXNU / uefi-enuma / Mu-Silicium base / UniXNU-sheng 四个 SHA），
   并在 `scripts/build-enuma-uefi.sh` 里加"pin 不匹配就拒绝"的守卫（照 umi `setup-workspace.py`）。
2. **建树后立刻**跑 `python3 scripts/check-enuma.py --tree <build-tree>`（验证 archives 可解析）
   与 `--uefi <checkout>`（验证 enumaPkg + 六补丁清单）。
3. **每完成一个阶段**就往 `docs/validation.json` 追加一条记录（照 umi 的 schema，**否定项也写**），
   不要只在 README 里写结论。
4. 触摸阶段**不要**参考 umi 的 FTS 路径；我们的参考是 astide `nt36523/` + Novatek SPI 事实（见 `../astide-notes.md` §1）。
5. 显示阶段采用 umi 的 `--display-profile inherited` 思路（继承 ABL 帧缓冲），
   这与我方阶段 3 defconfig 的 `NATIVE_LEGACY_DISPLAY=y / NATIVE_MOBILE_DISPLAY=n` 一致。
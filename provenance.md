# 数值来源与验证状态（enuma 板级配置）

每一处非显然的值都标明**来源**与**当前验证到哪一步**。这样做的直接原因：同 SoC 的 `pipa` 端口
**从未上机、也从未真正编译过**（官方设备页原文："Nothing has run on a pipa."，且它的 defconfig 缺
`CONFIG_XNU_BUILD_ARCHIVES`，用它的配置构建会在内核链接期失败——我们在 GitHub Actions 上复现过）。
所以"来自 pipa"≠"已验证"。

验证等级：**V1** 云端真构建通过 ｜ **V2** 有真机（他机）证据 ｜ **V3** 来自公开一手来源（DTS/官方文档） ｜ **待验** 只能等本机实机

| 配置项 | 值 | 来源 | 状态 | 本机验证方法 |
|---|---|---|---|---|
| `[payload] dram_size` | `0x200000000` | **本机实测**（`/proc/iomem` 最高 bank `0x280000000` − `0x80000000`） | V1 | ✅ 已由 `blockdev`/`MemTotal` 交叉验证 |
| `[userdata] first/last_lba` | `0x290000` / `0x3b037fa` | **本机实测**（sysfs start/size ÷8） | V1 | ✅ 与 `blockdev --getsize64` 一致（242,455,916,544 B） |
| `[userdata] lba_bytes` | `4096` | 本机实测 `blockdev --getss` | V1 | ✅ |
| `[ipsw]` 身份与成员 pin | iPad7,1 / 17.7.11 / 21H461 | 官方 IPSW（Apple CDN 实测 200） | V1 | ✅ 云构建中 OS/SystemOS 的 sha256 与 pin 一致 |
| `[kernel.prelink]` roots 与 `kernelcache_sha256` | 31 个 kext + `f18b4df8…` | pipa（未上机）＋ 本机同一份 IPSW | V1 | ✅ 云端 prelink 成功，`kernel.macho` 有哈希 |
| `[kernel] archives` 9 条 | 见 README §5b | pipa 列表 ＋ 我们补齐来源 | **V1** | ✅ 云端 9/9 全部构建成功（含我们修的 glob 与 `BUILD_ARCHIVES`） |
| defconfig 基线（CPU/GIC/timer/DT platform/corecrypto/pthread/AES/PMGR/link-stubs） | 见 `configs/enuma_defconfig` | pipa 基线 ＋ 我们补 `CONFIG_XNU_BUILD_ARCHIVES=y` | **V1** | ✅ 云端内核编译 + prelink 通过 |
| `[bootimg.image] header_version` | `1` | **umi（小米 10，SM8250 真机跑通）**＋ pipa 同值 | V2 | 待验：平板 ABL 若拒绝就用 `0`（`check-enuma.py` 已给回退说明） |
| `[bootimg.image] pagesize/kernel_offset/tags_offset` | `4096`/`0x8000`/`0x1e00000` | cas（真机到菜单）显式值 ＋ pipa 注释 | V2 | 待验：stock `boot.img` 头 |
| `[firmware] uefi_fd base/size`（UEFI 骨架里） | `0x9fc00000`/`0x300000` | **umi 真机**（"matched to the uefiplat.cfg from this phone's xbl_5"）＋ pipa 同值 | V2 | 待验：从 `enuma-uefi.img` 的 `uefiplat.cfg` 读 |
| ADT SoC 常量（DRAM base / arm-io / GIC / timebase / 核编号） | 见 `make-enuma-adt.py` 注释 | **astide 内核树**（`kona.dtsi:374/643-650/704/59-244`）＋ pipa | V3＋V1 | ✅ 云构建生成 ADT 13,456 B 并通过结构校验；待验：实机 `/cpus`、`/arm-io` 是否被 XNU 接受 |
| ADT 捐赠身份（iPad7,1 / J120 / 2732 / P3 / 按钮 hold,voldown,volup） | 见脚本 | **sheng/pipa 同构做法**（我们跑的是 iPad7,1 的 userland） | V3 | 待验：`getevent -pl` 复核按钮名 |
| ADT root `compatible` | `xiaomi,enuma\0qcom,sm8250\0` | 自建板身份（与 pipa 同构） | V3 | ⚠️ 实测 stock DT 根 compatible 是 `qcom,kona-mtp qcom,kona qcom,mtp`（**不含** `xiaomi,enuma`）；这是 XNU 侧身份，不是抄 stock |
| `SOC_TYPE = 2`（UEFI 骨架） | `2` | **只有 pipa 这么写**（三模板里唯一声明 SM8250-AC） | **待验** | 固件构建时与 `KonaPkg.dsc.inc` 的 SoC 选择核对 |
| 6 条 DXE 补丁（ButtonsDxe/UsbConfigDxe/TzDxeLA/FeatureEnablerDxe/UsbMsdDxe/DALSYSDxe） | 见 `uefi-enuma-skeleton/README.md` | `Device-Binaries/pipa/README.md`（**手机**的 drop） | **待验** | 应用于 enuma 自己的 drop 后，菜单/存储/USB 是否正常 |
| `LinkStubs.c` / `ShengPlatformPower.cpp` | 逐字取自 pipa | pipa（未上机） | V1（编译）／待验（语义） | ✅ 云端编译通过；PMGR 桥首建走 no-op stub，第二次构建才链接真桥 |
| `[firmware] submodules` 与 DSC 细节 | 见骨架 | pipa + 官方 `docs/porting/uefi-firmware.md` | 待验 | 固件步骤 |

## 结论

- **已经没有"来自 pipa 且未验证"的关键数值**：pipa 来源的两项（`archives` 列表、defconfig 基线）都已在云端真构建验证；
- 剩下标"待验"的，全都需要**本机镜像**（`uefi`/`xbl` dump）或**实机**（ABL 接受度、按钮、固件构建）——这正是下一步；
- pipa 给我们的真正价值是**结构模板与数值线索**，不是"可用路径"；同 SoC 上**真机跑通过**的参照是 **umi**（到 launchd + 持久 Data）与 **cas**（到菜单）。
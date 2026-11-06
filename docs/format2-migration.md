# 格式 1 → 格式 2 迁移（**已完成**，`main` 已是 schema 2）

> 2026-11-05：`main` 由 `format2` 快进合并（`a82f00b`）。放行的唯一依据是那次**完整**的 CI run
> `42069850732` —— 它的 "Assert the PMGR bridge is not a no-op stub" 步骤通过，`sheng_pmgr_run`
> **439 条指令**（与格式 1 已知可用内核同值），payload 里带了由板级 DTS 生成的 ADT。
> 格式 1 的板级文件留在 `legacy/board.toml.format1`，随时可对照。

## 为什么必须迁

不是为了好看。格式 1 在 `scripts/unixnu/cli.py` 里写着：

```python
patch_order = patches.plan(ctx, modules) if ctx.schema_version == 2 else []
```

**格式 1 没有内核补丁通道**。而我们需要至少一条设备级补丁：`native-smp-secondary.patch`
（`MAX_CPUS 4→8`，并让 BCM2837 机器配置按 kona 的编号方式从 MPIDR **AFF1** 取核号）。
这正是我们 8 核挂死在 `ml_parse_cpu_topology` 的原因，也是迄今每张镜像都带 `cpus=1` 的原因。

## 迁移结果（CI 实测）

| 步骤 | 结果 |
|---|---|
| `unixnu fetch` / `tree` / `release --check` | ✅ |
| Kernel pass 1（编译记录 + 首次内核） | ✅ |
| Kernel pass 2（PMGR 桥对真内核链接） | ✅ |
| 桥断言 | ✅ `sheng_pmgr_run has 439 instructions`，kernel.macho 52,011,008 B |
| Payload（板级 DTS → ADT → payload.img） | ✅ 64 MiB |

## 本地已完成（不需要 CI）

| 项 | 状态 |
|---|---|
| `feeds.toml` 钉 `ht94911/UniXNU-sm8250-common` @ `31af31c6` | ✅ 唯一的 SM8250 SoC 包 |
| `board.toml` 改成 `schema_version = 2`（原文件移入 `legacy/board.toml.format1`） | ✅ |
| `patches/xnu/native-smp-secondary.patch` + `patches/xnu/series` | ✅ 已对该 pin 的 XNU 源树 `git apply --check` 通过（hunk 偏移 -2 行） |
| `dts/enuma.dts` 改写成格式 2 设备 DTS（48 行：include SoC dtsi + DRAM/identity/使能 UFS），原合并树留作 `dts/reference/enuma-merged.dts` | ✅ |
| 用共享 ADT 生成器本地生成 ADT：`generate(dtb, board, unique_chip_id)` → **13,336 B**，含 `arm-io`(intc/ufshc/ufsphy)、`cpus`、`psci`、`chosen`(dram-base/size、unique-chip-id、darwinboot-memory-size、root-matching)、`product`、`buttons` | ✅ |
| `unixnu fetch`（拉 SoC/os/arch 源） | ✅ |
| `unixnu status --json` | ✅ 四个 profile 全部解析到：arch=arm64、soc=sm8250-common、os=ipados-17.7.11-21H461、device=本仓 |
| `unixnu defconfig enuma_ufs_defconfig` | ✅ arch+soc+device 三段 config 合并成 `.config` |

## 本地已跑通（不需要 CI，用钉住的 UniXNU + 本机的 fd/payload 输入）

| 步骤 | 结果 |
|---|---|
| dtc 编译 `dts/enuma.dts`（`-i soc:dts`） | ✅ 2,793 B dtb |
| 共享生成器产出 ADT | ✅ 13,336 B，含 arm-io(intc/ufshc/ufsphy)、cpus、psci、chosen(dram/unique-chip-id/root-matching)、product、buttons |
| 信托缓存 bundle（`make-trust-bundle-from-members.py`） | ✅ 61,356 B，sha256 `7416ca4f…` **与板级白名单一致** |
| `unixnu build payload`（含 ADT + panic 屏补丁） | ✅ `payload.img` 64 MiB |
| `unixnu bootimg`（fd + 合并树 dtb） | ✅ `boot.img` 68,661,248 B，`ANDROID!` 魔数、ramdisk == payload |

也就是说格式 2 的**除内核编译之外**整条链都验证过了；只剩 macOS runner 上的 `build kernel`。

## 待办（按顺序）

1. **`scripts/check-enuma.py` 适配格式 2**：`archives` 里现在混有 `component:<ID>`，检查器要有对应规则；
   `enuma_defconfig` 与 `soc:configs/sm8250.config` 里重复的 `CONFIG_XNU_ARMCPU` / `NATIVE_ARCH_TIMER` 去重。
2. **`[bootimg].dtb` 的产出**：`scripts/fetch-enuma-dts.py` 目前写到旧路径，要挂到 `[firmware].preflight`
   或改成 `build/enuma/enuma.dtb`。
3. ~~**ADT 归位**~~ ✅ 已解决：格式 2 的 ADT 由 `devicetree.generate()` 从**编译后的 DTS + board.toml** 生成，
   我们原来 `make-enuma-adt.py` 的参数逐项都有了着落 ——
   `chip_id`/`owned_memory_size`/`root_match_volume_uuid`/`packed_data_ramdisk` 走 `[payload.adt]`，
   `psci` 与 CPU 亲和性来自 DTS（`/psci` + `/cpus` 的 `reg`），iPad 身份走 `[adt.product]`。
   `cpu_regs` 不再需要：SoC dtsi 的 CPU `reg` 就是 kona 的 AFF1 编号。
4. **`unixnu tree` + `build kernel` 在 CI 里绿**（格式 2 的 kernel.yml 与我们 fork 的那份不同）。
5. 之后才把 UFS/存储挪到格式 2 轨道：`component:ShengUfs` + storage ABI 审计（原来的
   `storage_audit` 手工 CI 可以退休）。

## 为什么公有流水线不跑 `unixnu release`

`release` 的步骤是 `(kernel, package, firmware, payload)`。其中 **firmware 步骤需要两样本仓不携带的东西**：

1. `enumaPkg` overlay（`Tlrenhb/uefi-enuma`）；
2. 小米官方 fastboot ROM 里抽出来的 vendor drop（`xbl.elf` → `Binaries/enuma/`）。

这两样是专有材料，只走私有流水线。所以公有仓的格式 2 job 用显式步骤证明**真正有风险的那段**：

```
build kernel   （第一遍：产出编译记录）
build kernel   （第二遍：PMGR 桥对着真内核链接）
断言 _sheng_pmgr_run 指令数 ≥ 50（空桩不许过）
build payload  （ADT 由板级 DTS 生成）
```

boot 镜像仍在有固件的地方打（私有流水线或本地 `scripts/ufs-round.sh`）。

## 切换清单（main → 格式 2）

`scripts/switch-to-format2.sh` 把这一步做成一条命令，并且**只在格式 2 流水线有绿色 run 时才肯执行**：

1. 检查最近 8 个 run 里是否存在 `format2`+`success`；
2. 拒绝脏工作区；
3. `git merge --ff-only format2` 到 `main` 并推送。

## 切换时必须同时做的一件事：钉住私有固件流水线的设备版本

私有仓（`Tlrenhb/enuma-build`）的 `build-firmware.yml` 以 `ref: main` 检出设备仓，并依赖格式 1 的语义与路径：

- `./unixnu build package` / `./unixnu build firmware --uefi …`（格式 1 的 firmware 步骤）；
- `device/scripts/fetch-enuma-dts.py --fetch` → `device/out/astide/enuma.dtb`；
- boot 镜像用 `--dtb device/out/astide/enuma.dtb` 打包。

`main` 一旦切成 schema 2，这些都会变（`[firmware]` 的 dsc/fd/packages、dtb 产出于构建树）。所以切换时要把它的设备检出钉到格式 1 的最后一个提交：

```yaml
      - name: Check out the device repository (public)
        uses: actions/checkout@v4
        with:
          repository: Tlrenhb/UniXNU-enuma
          ref: f8f76c387f55f49195be69f9ebcc4ee412ea0986          # 格式 1 的最后一个提交；等固件流水线迁到格式 2 再改回 main
          path: device
```

私有固件流水线的格式 2 迁移作为后续任务（它需要 `[firmware]` + overlay + vendor drop 的路径重新对齐）。

### 已退役：本仓的 `scripts/build-arm-driver.py`

格式 2 里 `component:SoftwareAES` 会调 `build-arm-driver.py SoftwareAES`，而 UniXNU 主仓那份通过
`components.toml` 的 catalog 把 `SoftwareAES` 映射回 legacy 名 `NativeAES`。我们 vendored 的副本没有这个映射
（它的 `--component` 只认 `NativeAES` 等旧名），于是第一次 pass 报：

```
build-arm-driver.py: error: argument component: invalid choice: 'SoftwareAES'
```

那份副本原本只为了编 `EnumaUfsProbe.cpp`（格式 1 的只读探针），格式 2 用不上，所以在 `format2` 分支删掉，
让主仓的构建器接管。`build-link-stubs.py` 与 `build-platform-power.py` 仍保留（主仓没有）。

## 阶段 2 的存储绑定是**硬件门禁**，不是代码问题

`scripts/audit-storage-interface.py` 末尾要求一份**真机观测**：

```
build/darwinboot-storage-native-metadata/gdb-stop.json
  kernel_sha256            必须等于 prelink 候选的 sha256
  memory["<meta>:0x50"]    原始 IOService/IOBlockStorageDevice 元类内存
  system_registers         TCR_EL1 / SCTLR_EL1 / TTBR0_EL1 / TTBR1_EL1
  bounded_timeout_interrupted = false
```

这是上游"合格存储 ABI"的定义方式：在启动起来的 prelinked 内核里用 GDB 停下，读元类内存和页表寄存器。
**CI 无法产出它**，所以：

- 在拿到这份记录之前，`storage_audit=true` 的 run 会打印一条 notice（说明需要什么）并跳过；
- `component:ShengUfs` 进 archives、`CONFIG_XNU_NATIVE_UFS=y` 都等这份记录；
- 记录一旦有（真机 GDB 会话 + 提交到设备仓 `build/`），审计 → `build-enuma-ufs-bindings.py` → 归档 → 打开 UFS，一条链就通了。

### 实机取证清单（照抄即可）

CI 的 `Print the addresses the live storage record must contain` 步骤会打印本次构建的确切地址。最近一次
（run `42135112172`，2026-11-06 00:29 UTC）：

```
candidate sha256         : 2e174a3cf4821c87fb93b6b3321787b9a2aaa991406ffe0331aaa9947444fc69
storage vtable           : 0xfffffff005cb44c0
IOBlockStorageDevice meta: 0xfffffff006add1d0    <- 读 0x50 字节
IOService meta           : 0xfffffff007b01538    <- 读 0x50 字节
```

在平板上（prelinked 内核跑起来之后）：

```
x/80xb 0xfffffff006add1d0
x/80xb 0xfffffff007b01538
info registers tcr_el1 sctlr_el1 ttbr0_el1 ttbr1_el1
```

把 gdb 输出存成文件，然后：

```
python3 scripts/emit-storage-abi-addresses.py \
  --candidate <候选.macho> --plan <候选.json> --gdb-log gdb.txt \
  --out build/darwinboot-storage-native-metadata/gdb-stop.json
```

生成的 JSON 与 CI 打印的 sha256/地址一致（脚本会校验），提交到设备仓 `build/` 后重跑
`storage_audit=true` 即可继续。

**注意**：地址随构建而变（每次 prelink 的候选不同），所以每次都要用当次 CI 打印的那组，不要复用旧值。

## 迁移踩过的坑（每条都对应一次 CI 失败）

| 现象 | 根因 | 处置 |
|---|---|---|
| job 绿但产物没出来 | `run: … \| tee log` 的退出码是 `tee` 的 | 所有 release 步骤加 `set -euo pipefail` |
| `patch does not apply` @ `bsd_init.c:773` | os 系列的 `zzz-boot-progress.patch` 给 `sheng_pmgr_run()` 加插桩，而那个调用点来自 **sm8550 包**里的 `sheng-platform-power.patch`，我们的 sm8250 包没有 | 设备系列收进该补丁，顺序改 arch → soc → **device → os** |
| `AMFI: No PMGR?`（潜在） | 同上：桥编出来但没人调用 | 同上 + 两遍 kernel 构建 + `_sheng_pmgr_run` 指令数断言 |
| `component SoftwareAES is not selected` | `archives` 引用而 `[components] include` 未列 | 加入 include（四个 `component:` 引用逐一核对） |
| `invalid choice: 'SoftwareAES'` | 本仓 vendored 的 `build-arm-driver.py` 只认 legacy 名，主仓那份才有 catalog 映射 | 格式 2 分支删除该副本 |
| `IOPlatformExpert.cpo.json` 找不到 | 审计脚本要在**装配好的树**里跑 | 路径改到 `out/enuma/tree` |
| `Couldn't open dts/enuma.dts` | `git mv` 移错了文件 | 板级 DTS 与合并树各归其位 |
| `trust cache … is not one of the trust caches this board accepts` | 白名单钉的是 **bundle** 的 sha256，不是原始成员 | 先跑 `make-trust-bundle-from-members.py` 再传给 payload |
| `release` 卡在 firmware | `release` 步骤含 firmware，需要 overlay + vendor drop（专有） | 公有流水线只做 kernel×2 + payload，boot 镜像留在私有侧 |

## 迁移后可以退役的东西

- `drivers/ufs/`（vendor 的 ShengUfs 拷贝 + 手写探针）→ 由 `component:ShengUfs` 取代；
- `kernel.yml` 里我们自己拼的那些步骤 → 格式 2 的 `package.yml` / 上游工作流；
- `configs/enuma_{display_touch,owner}_defconfig` 里与 arch/soc 重复的部分。

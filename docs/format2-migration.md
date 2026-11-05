# 格式 1 → 格式 2 迁移（分支 `format2`）

## 为什么必须迁

不是为了好看。格式 1 在 `scripts/unixnu/cli.py` 里写着：

```python
patch_order = patches.plan(ctx, modules) if ctx.schema_version == 2 else []
```

**格式 1 没有内核补丁通道**。而我们需要至少一条设备级补丁：`native-smp-secondary.patch`
（`MAX_CPUS 4→8`，并让 BCM2837 机器配置按 kona 的编号方式从 MPIDR **AFF1** 取核号）。
这正是我们 8 核挂死在 `ml_parse_cpu_topology` 的原因，也是迄今每张镜像都带 `cpus=1` 的原因。

## 已完成（本地可复现）

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

## 切换清单（main → 格式 2）

`scripts/switch-to-format2.sh` 把这一步做成一条命令，并且**只在格式 2 流水线有绿色 run 时才肯执行**：

1. 检查最近 8 个 run 里是否存在 `format2`+`success`；
2. 拒绝脏工作区；
3. `git merge --ff-only format2` 到 `main` 并推送。

## 迁移后可以退役的东西

- `drivers/ufs/`（vendor 的 ShengUfs 拷贝 + 手写探针）→ 由 `component:ShengUfs` 取代；
- `kernel.yml` 里我们自己拼的那些步骤 → 格式 2 的 `package.yml` / 上游工作流；
- `configs/enuma_{display_touch,owner}_defconfig` 里与 arch/soc 重复的部分。

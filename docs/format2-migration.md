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
| `dts/enuma.dts`（合并后的完整设备树） | ✅ 42,009 行 |
| `unixnu fetch`（拉 SoC/os/arch 源） | ✅ |
| `unixnu status --json` | ✅ 四个 profile 全部解析到：arch=arm64、soc=sm8250-common、os=ipados-17.7.11-21H461、device=本仓 |
| `unixnu defconfig enuma_ufs_defconfig` | ✅ arch+soc+device 三段 config 合并成 `.config` |

## 待办（按顺序）

1. **`scripts/check-enuma.py` 适配格式 2**：`archives` 里现在混有 `component:<ID>`，检查器要有对应规则；
   `enuma_defconfig` 与 `soc:configs/sm8250.config` 里重复的 `CONFIG_XNU_ARMCPU` / `NATIVE_ARCH_TIMER` 去重。
2. **`[bootimg].dtb` 的产出**：`scripts/fetch-enuma-dts.py` 目前写到旧路径，要挂到 `[firmware].preflight`
   或改成 `build/enuma/enuma.dtb`。
3. **ADT 归位**：`make-enuma-adt.py` 的特例（`chip_id`、`cpu_regs 0,0x100..`、`psci=smc`、iPad 身份、
   root 卷 UUID）要么落进 `[adt.*]`，要么作为设备脚本保留并被 `[payload]` 引用 —— 需要对照格式 2 的
   共享 ADT 生成器确认哪些已内置。
4. **`unixnu tree` + `build kernel` 在 CI 里绿**（格式 2 的 kernel.yml 与我们 fork 的那份不同）。
5. 之后才把 UFS/存储挪到格式 2 轨道：`component:ShengUfs` + storage ABI 审计（原来的
   `storage_audit` 手工 CI 可以退休）。

## 迁移后可以退役的东西

- `drivers/ufs/`（vendor 的 ShengUfs 拷贝 + 手写探针）→ 由 `component:ShengUfs` 取代；
- `kernel.yml` 里我们自己拼的那些步骤 → 格式 2 的 `package.yml` / 上游工作流；
- `configs/enuma_{display_touch,owner}_defconfig` 里与 arch/soc 重复的部分。

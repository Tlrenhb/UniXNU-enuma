# scripts/ 状态表（骨架）

| 脚本 | 状态 | 说明 / 下一步 |
| --- | --- | --- |
| `make-enuma-adt.py` | 可运行（骨架） | `--print-todo` 列出所有必须实机测量的板级值；未填齐时**拒绝生成**（退出码 2）。SoC 常量（GIC/timebase/DRAM base/核心编号）已填，来源 `sm8250.dtsi`（经 pipa 验证）。填齐后：`python3 scripts/make-enuma-adt.py --dram-size … --armio-base … --root-compatible … --product-name … --artwork-device-subtype … --button-names … --output …` |
| `check-enuma.py` | 可运行 | 离线预检：占位符清单、IPSW 身份、defconfig 是否混入 SM8550 专属开关；`--uefi DIR` 时检查 enumaPkg 集成点。**未实现**：MemoryMapLib 描述符与 DTB splash 检查（照 pipa 的 `check-pipa.py` 移植）。 |
| `build-enuma-uefi.sh` | 骨架 | 固件迭代回路（照 pipa `uefi-round.sh`）。TODO：串口、`product: enuma` 校验、日志读取脚本。 |
| `fetch-enuma-dts.py` | 骨架 | 拒绝在没有 pin 的情况下抓取。mainline 只有 elish（Pad 5 Pro）树，**无 enuma 树**；先确认面板变体（boe/csot）。 |
| 通用脚本（待加入） | 未加入 | 从 UniXNU-sheng **原样复制**：`build-arm-driver.py`、`build-platform-power.py`、`build-link-stubs.py`、`patch-xnu-entry-probe.py`、`xnu-panic-screen.c`（pipa 证明它们跨板通用）。这些在主仓库/参照仓库里，等 P1 阶段再拷进来并记录来源。 |

**注意（shell 传参）**：Apple 多字符串（compatible、button-names）在 bash 里**不能**带 NUL，所以这两个参数用 `;`
分隔，脚本内部转成 `\0`：

```sh
python3 scripts/make-enuma-adt.py \
  --dram-size 0x100000000 --armio-base 0x17000000 \
  --root-compatible "xiaomi,enuma;qcom,sm8250" \
  --product-name "Xiaomi Pad 5 Pro 5G" --artwork-device-subtype 2560 \
  --button-names "hold;voldown;volup" \
  --unique-chip-id 0x... --chip-id 0xcf --ipad-identity --packed-data-ramdisk --trust-cache \
  --output build/enuma/boot/kernel.macho.adt
```

（上面的数值都是**占位示例**，不是测量结果——实机命令见 `../porting-plan.md` §4。）

## make-trust-bundle-from-members.py

从驱动已提取的 trustcache 成员组装 original-static-system bundle（上游脚本要完整 6.37 GiB IPSW）。
对每个成员枚举候选（裸字节 + IM4P 各字段）按哈希匹配，失败时打印候选哈希表；最后用 board 白名单校验 bundle。

#!/usr/bin/env python3
"""enuma-fill.py — 把 enuma-probe.sh 的采集结果回填进设备仓库骨架的 TODO 占位符。

设计原则：
  * 只填"确实测到了"的值；测不到的一律保留 TODO，并在报告里列出获取方法。
  * 不猜、不造。每次改动都在行尾写 `# filled by enuma-fill (<来源>)`。
  * 幂等：可重复运行；已填过的行会被新值覆盖（并保留注释）。

用法：
  python3 scripts/enuma-fill.py --probe enuma-probe-YYYYmmdd-HHMMSS --skeleton UniXNU-enuma-skeleton
  python3 scripts/enuma-fill.py --probe <dir> --skeleton <dir> --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FILL_NOTE = "filled by enuma-fill"


def hex_or_none(value):
    if value is None:
        return None
    if isinstance(value, int):
        return hex(value)
    text = str(value).strip()
    if text.lower().startswith("0x"):
        return text
    return "0x%x" % int(text, 0) if re.fullmatch(r"[0-9a-fA-F]+", text) else None


def section_of(line: str) -> str | None:
    m = re.match(r"\s*\[\[?([^\]]+)\]\]?", line)
    return m.group(1).strip() if m else None


def replace_key(lines: list[str], section: str, key: str, value: str, source: str) -> tuple[bool, str]:
    """在给定 section 内替换 `key = <old>` 的值。返回 (是否替换, 说明)。"""
    current = None
    for i, line in enumerate(lines):
        s = section_of(line)
        if s is not None:
            current = s
            continue
        if current != section:
            continue
        m = re.match(rf"(\s*{re.escape(key)}\s*=\s*)([^#\n]*?)(\s*)(#.*)?$", line)
        if not m:
            continue
        old = m.group(2).strip()
        if old == value:
            return True, f"{section}.{key} 已是 {value}（未改动）"
        lines[i] = f"{m.group(1)}{value}  # {FILL_NOTE} ({source})"
        return True, f"{section}.{key}: {old} -> {value}  [{source}]"
    return False, f"{section}.{key} 未找到（骨架结构可能已变，请手动确认）"


def derive_dram_size(summary: dict) -> tuple[str | None, str]:
    """按 pipa 的做法：最高 System RAM 段末尾 - 0x80000000。"""
    entries = summary.get("iomem_system_ram_entries") or []
    if not entries:
        return None, "无 iomem System RAM 数据"
    ends = []
    for pair in entries:
        try:
            ends.append(int(pair[1], 16))
        except (ValueError, IndexError):
            continue
    if not ends:
        return None, "iomem 解析失败"
    end = max(ends) + 1          # iomem 打印闭区间末字节，换算成 bank 末尾（exclusive）
    return hex(end - 0x80000000), f"由最高 System RAM 段末尾 0x{end:x} − 0x80000000 推出（与 pipa 同法，需人工复核）"


def derive_splash(probe: Path) -> tuple[str | None, str]:
    for name in ("enuma-dtbo.dts", "enuma-dtb.dts"):
        f = probe / name
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"cont_splash_region@?[0-9a-f]*\s*\{(.*?)\}", text, re.S)
        if not m:
            m = re.search(r"cont_splash_region[^}]*\{(.*?)\}", text, re.S)
        if m:
            reg = re.search(r"reg\s*=\s*<([^>]+)>", m.group(1))
            if reg:
                cells = reg.group(1).split()
                return " ".join(cells), f"来自 {name} 的 cont_splash_region reg"
    return None, "dtbo/dtb 反编译结果里没找到 cont_splash_region（先确认 dump 成功、dtc 可用）"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe", type=Path, required=True, help="enuma-probe.sh 的输出目录")
    ap.add_argument("--skeleton", type=Path, required=True, help="设备仓库骨架目录")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    args = ap.parse_args()

    summary_path = args.probe / "summary.json"
    if not summary_path.exists():
        print(f"error: {summary_path} 不存在（先跑 scripts/enuma-probe.sh）", file=sys.stderr)
        return 2
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    board = args.skeleton / "board.toml"
    if not board.exists():
        print(f"error: {board} 不存在", file=sys.stderr)
        return 2

    todos_before = len(re.findall(r"TODO\(enuma\)", board.read_text(encoding="utf-8")))
    lines = board.read_text(encoding="utf-8").splitlines()
    results: list[str] = []
    pending: list[str] = []

    def attempt(section, key, raw_value, source):
        if raw_value is None:
            pending.append(f"{section}.{key}: {source}")
            return
        ok, msg = replace_key(lines, section, key, raw_value, source)
        (results if ok else pending).append(msg)

    attempt("userdata", "first_lba", hex_or_none(summary.get("userdata_first_lba")), "sgdisk 实测 GPT")
    attempt("userdata", "last_lba", hex_or_none(summary.get("userdata_last_lba")), "sgdisk 实测 GPT")
    lba_bytes = summary.get("userdata_lba_bytes")
    attempt("userdata", "lba_bytes", str(lba_bytes) if lba_bytes else None, "blockdev --getss（十进制，与 sheng/pipa 一致）")

    dram, dram_src = derive_dram_size(summary)
    attempt("payload", "dram_size", f'"{dram}"' if dram else None, dram_src)   # 与 sheng/pipa 一致：字符串

    splash, splash_src = derive_splash(args.probe)
    if splash:
        results.append(f"[bootimg.dtb_node] splash_region reg 读数为: {splash}  [{splash_src}] —— 结构为数组，请手动确认写入")
    else:
        pending.append(f"splash_region: {splash_src}")

    if not args.dry_run:
        board.write_text("\n".join(lines) + "\n", encoding="utf-8")

    todos_after = len(re.findall(r"TODO\(enuma\)", "\n".join(lines)))
    report = [
        "# enuma-fill 回填报告",
        "",
        f"- 骨架：`{board}`",
        f"- 采集：`{summary_path}`",
        f"- TODO 数量：{todos_before} → {todos_after}（已填 {todos_before - todos_after}）",
        f"- 模式：{'dry-run（未写盘）' if args.dry_run else '已写入'}",
        "",
        "## 已回填",
        "",
    ] + ([f"- {r}" for r in results] or ["- （无）"]) + [
        "",
        "## 仍需人工处理",
        "",
    ] + ([f"- {p}" for p in pending] or ["- （无）"]) + [
        "",
        "## 剩下的 TODO 获取方法",
        "",
        "```sh",
        "adb shell su -c 'sgdisk -p /dev/block/sda'          # userdata LBA",
        "adb shell su -c 'cat /proc/iomem'                   # DRAM bank / 可用窗口",
        "adb shell su -c 'dd if=/dev/block/by-name/dtbo of=/data/local/tmp/dtbo.img'  # splash / 面板",
        "adb shell getevent -pl                              # 触摸与侧键",
        "```",
        "",
    ]
    out = args.probe / "fill-report.md"
    if not args.dry_run:
        out.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    print(f"\n报告：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
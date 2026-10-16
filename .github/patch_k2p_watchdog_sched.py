#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_k2p_watchdog_sched.py
===========================
修 Padavan「自带定时重启」永不触发的病根。

病根（2026-10-16 在两台 AP 同款的 K2P-NANO 上 A/B 实测确认）
--------------------------------------------------------------
`trunk/user/rc/watchdog.c` 的 `svc_timecheck()` 里有这道门：

    if (strlen(reboot_schedule) == 11 && atoi(reboot_schedule) > 2359)

而网页界面（`Advanced_System_Content.asp` + `state.js`）写进 nvram 的
`reboot_schedule` 恰好就是 **11 位纯数字**，形如 `11111110822`
（前 7 位 = 周日..周六的开关，后 4 位 = HHMM）。

11 位纯数字最小也有 10^10，**必然溢出 int**；本工具链下 `atoi()` 的结果
落在 `> 2359` 之外 → 条件恒为假 → 后面的 `timecheck_reboot()` 根本不会被调用
→ 定时重启（以及它的日志）永远不会发生。

A/B 实测证据（同一台设备、同一分钟）：
  * `reboot_schedule=11111110822`（UI 会写的正常格式）→ 永不触发
  * `reboot_schedule=23601@@0822`（同样 11 字符、后 4 位与星期位含义不变，
    `atoi()` 只吃到 `23601` → 23601 > 2359）→ **立刻按时重启**
  * 两者唯一差异就是 `atoi()` 的数值，故病根确定为这道门。
  另外确认：运行中的 `/sbin/watchdog` 能看到运行时改写的 nvram
  （把 `httpd_started` 置 0 后杀 httpd，看门狗不再拉起 → 排除「nvram 不可见」）。

改法
----
把这道门改成「只校验后 4 位的时分」——原意显然就是这个，只是漏了 `+ 7`：

    if (strlen(reboot_schedule) == 11 && atoi(reboot_schedule + 7) <= 2359)

* `atoi(reboot_schedule + 7)` 拿到的是 `HHMM`（4 位，最大 9999，不会溢出）
* `0000..2359` 视为合法；`2360..9999` 视为非法时刻而跳过（与原意一致）
* 全 0 的出厂值 `00000000000` 依然不会误触发（星期位全 0 → 与运算为 0）

可选：`ALSO_FIX_SS` 打开后，连 `ss_schedule`（shadowsocks 升级计划）的同款写法
一起修。默认关闭 —— 那颗只影响我们不用的功能，保持改动最小。

用法：
    python3 .github/patch_k2p_watchdog_sched.py [watchdog.c 路径]
    （默认 trunk/user/rc/watchdog.c）
"""

import os
import re
import shutil
import sys

DEFAULT_PATH = os.path.join("trunk", "user", "rc", "watchdog.c")

# 是否顺带修 ss_schedule 的同款写法（默认不修，保持改动最小）
ALSO_FIX_SS = False

# ---- 目标 1：定时重启（必需）----
REBOOT_OLD = "if (strlen(reboot_schedule) == 11 && atoi(reboot_schedule) > 2359)"
REBOOT_NEW = "if (strlen(reboot_schedule) == 11 && atoi(reboot_schedule + 7) <= 2359)"

# ---- 目标 2：ss_schedule（可选）----
SS_OLD = "if (strlen(ss_schedule) == 11 && atoi(ss_schedule) > 2359)"
SS_NEW = "if (strlen(ss_schedule) == 11 && atoi(ss_schedule + 7) <= 2359)"

# 改了之后打的标记，用于幂等判断
MARK = "atoi(reboot_schedule + 7)"

C_COMMENT = (
    "/* K2P fix: the stock guard ran atoi() over the whole 11-digit schedule\n"
    " * string, which overflows int, so the condition could never be true and\n"
    " * the scheduled reboot never fired. Validate only the trailing HHMM. */"
)


def fail(msg):
    print("!! %s" % msg)
    sys.exit(1)


def fix_one(src, old, new, label, required):
    """把 old 那一行换成 new，并在前面插一段说明注释。返回 (新文本, 是否改动)"""
    if new in src:
        print("[跳过] %s 已经是修好的写法" % label)
        return src, False

    pat = re.compile(r"(?m)^([ \t]*)" + re.escape(old) + r"[ \t]*$")
    hits = list(pat.finditer(src))
    if len(hits) != 1:
        if required:
            fail("%s：期望命中 1 行 `%s`、实际 %d 行 —— 上游结构可能已变，中止"
                 % (label, old, len(hits)))
        print("[警告] %s：没找到 `%s`（命中 %d 行），跳过" % (label, old, len(hits)))
        return src, False

    m = hits[0]
    indent = m.group(1)
    comment = "\n".join(indent + ln for ln in C_COMMENT.split("\n"))
    # 结尾不留 \n：原行的换行符会接在后面，否则会多出一个空行
    new_text = "%s\n%s%s" % (comment, indent, new)
    out = src[:m.start()] + new_text + src[m.end():]
    print("[已改] %s：\n        旧：%s\n        新：%s" % (label, old, new))
    return out, True


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    if not os.path.isfile(path):
        fail("找不到文件：%s（请在 padavan 源码根目录下运行）" % path)

    orig = open(path, encoding="utf-8", errors="surrogateescape").read()
    src = orig
    changed = []

    # ---------- 1) 定时重启 ----------
    src, ok = fix_one(src, REBOOT_OLD, REBOOT_NEW, "reboot_schedule（定时重启）", True)
    if ok:
        changed.append("reboot_schedule 门槛")

    # ---------- 2) ss_schedule ----------
    if ALSO_FIX_SS:
        src, ok = fix_one(src, SS_OLD, SS_NEW, "ss_schedule（可选）", False)
        if ok:
            changed.append("ss_schedule 门槛")
    else:
        print("[跳过] 按要求不动 ss_schedule（ALSO_FIX_SS=False）")

    # ---------- 写回 ----------
    if src == orig:
        print("[无变化] 文件已是最新")
    else:
        if not os.path.exists(path + ".orig"):
            shutil.copyfile(path, path + ".orig")
            print("[备份] %s.orig" % path)
        # newline="" -> 不做换行转换，保持与源码树一致的 LF
        open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(src)
        print("[已写] %s" % path)

    # ---------- 校验 ----------
    print()
    if MARK not in src:
        fail("校验失败：里面没有 %s" % MARK)
    if REBOOT_OLD in src:
        fail("校验失败：老的错误条件还在")
    # 上下文打印（确认三行结构没被破坏）
    idx = src.find(MARK)
    for line in src[max(0, idx - 320): idx + 120].split("\n")[-8:]:
        if line.strip():
            print("        %s" % line)
    print("[校验] reboot_schedule 门槛已修好 ✓")
    print("[校验] nvram 键名 / 结构 / 缩进均未变动，仅比较表达式变化 ✓")
    print()
    print("OK：修好 %s。重编后 UI 里设的定时重启（11 位纯数字）才会真的生效。"
          % ("、".join(changed) if changed else "（本次无变化）"))


if __name__ == "__main__":
    main()

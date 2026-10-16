#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_k2p_wifi_defaults.py
==========================
把 Padavan 里 **K2P 的 5G 高级参数默认值** 对齐到「两台 AP 实测在用的那套配置」，
这样**恢复出厂 / 不保留配置刷机**之后，5G 出厂值就是我们要的，不用再手工去网页里点一遍。

改三处（都只对 K2P 生效，其它机型行为完全不变）
------------------------------------------------
1) `wl_HT_BW`  : 20/40/80 MHz(2)  ->  20/40 MHz(1)
   K2P 的 board.h 里有 `#define BOARD_HAS_5G_11AC 1`，所以出厂默认是 80MHz。
   但 K2P 是 2x2 MT7615D，5G 要同时当「同信道无线回程(apcli0 中继)」，80MHz 实测更差
   （主路由同批数据：40MHz 上行 92.4Mbps vs 80MHz 79.9~82.1Mbps，区间不重叠）。

2) `wl_wme`    : 1(开) -> 0(关)
   两台 AP 实测都是 `wl_wme=0`。这不是随便设的：
   主路由 QWRT 的驱动 profile（/tmp/profiles/dbdc.b1.dat，由 mtwifi 生成）里是
   `WmmCapable=0`，也就是**主路由侧 WMM 是关的**；AP 侧一旦开 WMM，
   作为 STA 去关联「不开 WMM 的 AP」会失败 —— 实测把 AP1 的 wl_wme 改成 1
   直接失联并触发看门狗重启。所以两边都关才是能工作的组合。
   注意：只改 5G 的 `wl_wme`，**不动 2.4G 的 `rt_wme`**（AP 上 2.4G 是 `rt_wme=1`）。

3) `wl_country_code` : 默认值 -> 固定 "CN"
   两台 AP 实测都是 CN。原默认是 `DEF_WLAN_5G_CC`（注释写着 "default obtained from driver"），
   这里给 K2P 钉死 CN，保证恢复出厂也是 CN。
   不想要这一条就把下面 PIN_COUNTRY_CN 改成 False。

4) `wl_auth_mode` / `rt_auth_mode` : "psk" -> "open"（两频段都默认不加密）
5) `wl_wpa_psk` / `rt_wpa_psk`   : 出厂预置口令 -> 空串
   出厂默认 WPA 口令是 `DEF_WLAN_5G_PSK` / `DEF_WLAN_2G_PSK`（= "1234567890"，
   一个人尽皆知的弱口令）。这两条把它删掉，让 2.4G / 5G **出厂即开放网络**（无密码）。
   做法照抄 Padavan 自己的访客网络默认值（`wl_guest_auth_mode="open"` +
   `wl_guest_wpa_psk=""`），是这套固件里已有、能正常工作的组合。

   ⚠️ 只影响「恢复出厂 / 不保留配置刷机」之后的初始状态：
      * 「保留配置」刷机时 nvram 不动，手上已设好密码的机器**完全不受影响**；
      * 也**不影响**无线回程（apcli0）—— 上级 SSID/口令走 `wl_sta_ssid` /
        `wl_sta_wpa_psk`，那是另一组键。
   ⚠️ 出厂开放意味着「恢复出厂后被别人蹭网」的风险自负。不想要就把
      OPEN_DEFAULT_WIFI 改成 False（恢复成原厂的 psk + 预置口令）。

**没有改的项**（逐条比对过，Padavan 出厂默认已经与 AP 实测值一致）：
  TxPower=100、wl_txbf=1、wl_ldpc=2、wl_mumimo=0、wl_HT_BAWinSize=256、wl_HT_AMSDU=1、
  wl_HT_AutoBA=1、wl_PktAggregate=1、wl_rts=2347、wl_frag=2346、wl_bcn=100、wl_dtim=1、
  wl_preamble=1、wl_HT_80211KV=1、wl_HT_80211R=0、wl_stream_tx/rx=2/2、wl_gmode=4、wl_greenap=0。
  （5G 的「短保护间隔 SGI」根本不是 nvram 项，由驱动默认开，无需固化。）

只改「没有该 nvram 项时写入的默认值」——设备上已手工设过的值不受影响。
幂等：已打过补丁则只做校验、不重复改。

用法：
    python3 .github/patch_k2p_wifi_defaults.py [defaults.c 路径]
    （默认路径 trunk/user/shared/src/defaults.c）
"""

import os
import re
import shutil
import sys

DEFAULT_PATH = os.path.join("trunk", "user", "shared", "src", "defaults.c")

# 钉死国家码 CN（不想钉就改成 False）
PIN_COUNTRY_CN = True

# 出厂 2.4G / 5G 默认开放（删掉预置口令 "1234567890"）；不想开放就改成 False
OPEN_DEFAULT_WIFI = True

# --- 1) 5G 信道带宽 ---
OLD_COND = "#if BOARD_HAS_5G_11AC"
NEW_COND = "#if BOARD_HAS_5G_11AC && !defined(BOARD_K2P)"
BW80_LINE = '{ "wl_HT_BW", "2" },'
BW40_LINE = '{ "wl_HT_BW", "1" },'
TXPWR_LINE = '{ "wl_TxPower", "100" },'


def fail(msg):
    print("!! %s" % msg)
    sys.exit(1)


def wrap_k2p(src, key, old_decl, new_decl, new_comment, old_comment):
    """把 defaults.c 里的某一行改成 `#ifdef BOARD_K2P / #else / #endif` 三行结构。

    old_decl / new_decl 是该行 `{ "key", value },` 部分（不含前后空白）；
    old_comment / new_comment 是行尾注释（保持缩进风格一致）。
    返回 (新文本, 状态字符串)。找不到行就 fail。
    """
    pat = re.compile(r"(?m)^([ \t]*)" + re.escape(old_decl) + r"([ \t]*)" + re.escape(old_comment) + r"[ \t]*$")
    hits = list(pat.finditer(src))
    if len(hits) != 1:
        fail("期望 `%s` 恰好命中 1 行、实际 %d 行 —— 上游结构可能已变，中止" % (old_decl, len(hits)))
    m = hits[0]
    indent = m.group(1)
    new_text = (
        "#ifdef BOARD_K2P\n"
        "%s%s%s%s\n"
        "#else\n"
        "%s%s%s%s\n"
        "#endif"
    ) % (indent, new_decl, m.group(2), new_comment,
         indent, old_decl, m.group(2), old_comment)
    out = src[:m.start()] + new_text + src[m.end():]
    return out, "已改：%s -> K2P 用 %s" % (old_decl, new_decl)


def wrap_k2p_any(src, old_decl, new_decl, new_tail, label):
    """同上，但**允许行尾没有注释**（2.4G 那两行就是没有）。

    行尾若有注释会原样保留在 #else 分支上；new_tail 是新分支自己的行尾注释。
    返回 (新文本, 状态字符串)。找不到行 fail。
    """
    pat = re.compile(r"(?m)^([ \t]*)" + re.escape(old_decl) + r"([ \t]*(?:/\*[^\n]*\*/)?)[ \t]*$")
    hits = list(pat.finditer(src))
    if len(hits) != 1:
        fail("%s：期望 `%s` 恰好命中 1 行、实际 %d 行 —— 上游结构可能已变，中止"
             % (label, old_decl, len(hits)))
    m = hits[0]
    indent, tail = m.group(1), m.group(2)
    new_text = (
        "#ifdef BOARD_K2P\n"
        "%s%s%s\n"
        "#else\n"
        "%s%s%s\n"
        "#endif"
    ) % (indent, new_decl, new_tail, indent, old_decl, tail)
    out = src[:m.start()] + new_text + src[m.end():]
    return out, "已改：%s -> K2P 用 %s" % (old_decl, new_decl)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    if not os.path.isfile(path):
        fail("找不到文件：%s（请在 padavan 源码根目录下运行）" % path)

    orig = open(path, encoding="utf-8", errors="surrogateescape").read()
    src = orig

    if BW80_LINE not in src:
        fail("源码里找不到 %s —— 上游结构可能已变，请人工核对" % BW80_LINE)
    if BW40_LINE not in src:
        fail("源码里找不到 %s（else 分支）—— 上游结构可能已变，请人工核对" % BW40_LINE)
    if TXPWR_LINE not in src:
        fail("源码里找不到 %s —— 5G 默认功率不是 100，请人工确认后再编译" % TXPWR_LINE)

    # 5G 参数必须都在 `#if BOARD_HAS_5G_RADIO` 段里；定位它以便后面做位置校验
    i_5g = src.find("#if BOARD_HAS_5G_RADIO")
    if i_5g < 0:
        fail("找不到 `#if BOARD_HAS_5G_RADIO` —— 无法确认 5G 参数段，中止")

    changed = []

    # ---------- 1) wl_HT_BW ----------
    if NEW_COND in src:
        print("[跳过] wl_HT_BW 条件已经带着 !defined(BOARD_K2P)")
    else:
        # 只替换「紧跟着 { "wl_HT_BW", "2" } 的那个 #if」，
        # 避免误改文件里其它 BOARD_HAS_5G_11AC 条件（如 5G 存在性开关）。
        pat = re.compile(
            r"(?m)^" + re.escape(OLD_COND) + r"[ \t]*\n(?=[ \t]*" + re.escape(BW80_LINE) + r")"
        )
        src, n = pat.subn(NEW_COND + "\n", src)
        if n != 1:
            fail("wl_HT_BW：期望命中 1 处、实际命中 %d 处 —— 中止，不做改动" % n)
        print("[已改] %s  ->  %s" % (OLD_COND, NEW_COND))
        changed.append("wl_HT_BW=1")

    # ---------- 2) wl_wme ----------
    if re.search(r'(?m)^[ \t]*\{ "wl_wme", "0" \},', src):
        print("[跳过] wl_wme 已经是 0")
    else:
        i_wme = src.find('{ "wl_wme", "1" },')
        if i_wme < 0 or i_wme < i_5g:
            fail("找不到 5G 段的 `{ \"wl_wme\", \"1\" },` —— 中止")
        src, msg = wrap_k2p(
            src,
            "wl_wme",
            '{ "wl_wme", "1" },',
            '{ "wl_wme", "0" },',
            "\t\t\t/* WME mode — K2P：主路由 QWRT 侧 WMM 也是关的，两边必须一致 */",
            "\t\t\t/* WME mode (off|on) */",
        )
        print("[%s]" % msg)
        changed.append("wl_wme=0")

    # ---------- 3) wl_country_code ----------
    if PIN_COUNTRY_CN:
        if re.search(r'(?m)^[ \t]*\{ "wl_country_code", "CN" \},', src):
            print("[跳过] wl_country_code 已经是 CN")
        else:
            src, msg = wrap_k2p(
                src,
                "wl_country_code",
                '{ "wl_country_code", DEF_WLAN_5G_CC },',
                '{ "wl_country_code", "CN" },',
                "\t\t/* Country Code — K2P：固定 CN */",
                "\t\t/* Country Code (default obtained from driver) */",
            )
            print("[%s]" % msg)
            changed.append('wl_country_code=CN')
    else:
        print("[跳过] 按配置不钉国家码")

    # ---------- 4) 5) 出厂 2.4G / 5G 删掉预置口令（默认开放）----------
    OPEN_JOBS = [
        ('{ "wl_auth_mode", "psk" },',
         '{ "wl_auth_mode", "open" },',
         "\t\t/* K2P：出厂默认不加密 */",
         "wl_auth_mode(5G)"),
        ('{ "wl_wpa_psk", DEF_WLAN_5G_PSK },',
         '{ "wl_wpa_psk", "" },',
         "\t\t\t/* K2P：出厂默认无密码 */",
         "wl_wpa_psk(5G)"),
        ('{ "rt_auth_mode", "psk" },',
         '{ "rt_auth_mode", "open" },',
         "\t\t/* K2P：出厂默认不加密 */",
         "rt_auth_mode(2.4G)"),
        ('{ "rt_wpa_psk", DEF_WLAN_2G_PSK },',
         '{ "rt_wpa_psk", "" },',
         "\t\t\t/* K2P：出厂默认无密码 */",
         "rt_wpa_psk(2.4G)"),
    ]
    if OPEN_DEFAULT_WIFI:
        for old_decl, new_decl, new_tail, label in OPEN_JOBS:
            if new_decl in src:
                print("[跳过] %s 已经是 %s" % (label, new_decl))
                continue
            src, msg = wrap_k2p_any(src, old_decl, new_decl, new_tail, label)
            print("[%s] %s" % (label, msg))
            changed.append(label)
    else:
        print("[跳过] 按配置保留出厂预置口令")

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
    print("[校验] wl_HT_BW 分支：")
    idx = src.find(NEW_COND)
    if idx < 0:
        fail("校验失败：找不到 %s" % NEW_COND)
    for line in src[idx: idx + 260].split("\n")[:6]:
        if line.strip():
            print("        %s" % line)

    if re.search(r'(?m)^[ \t]*\{ "wl_wme", "0" \},', src):
        print("[校验] wl_wme  -> 0（K2P 分支）✓")
    else:
        fail("校验失败：找不到 K2P 的 wl_wme=0")

    if PIN_COUNTRY_CN and not re.search(r'(?m)^[ \t]*\{ "wl_country_code", "CN" \},', src):
        fail("校验失败：找不到 K2P 的 wl_country_code=CN")

    if OPEN_DEFAULT_WIFI:
        for need in ('{ "wl_auth_mode", "open" },', '{ "wl_wpa_psk", "" },',
                     '{ "rt_auth_mode", "open" },', '{ "rt_wpa_psk", "" },'):
            if not re.search(r'(?m)^[ \t]*' + re.escape(need), src):
                fail("校验失败：找不到 K2P 的 %s" % need)
        print("[校验] wl/rt 双频段 auth_mode=open + 预置口令已清空 ✓")
        # 原始 psk 宏只应出现在 #else 分支里（即非 K2P）
        for macro in ("DEF_WLAN_5G_PSK", "DEF_WLAN_2G_PSK"):
            if src.count(macro) != 1:
                fail("校验失败：%s 出现 %d 次（应恰好 1 次，只在 #else 分支）"
                     % (macro, src.count(macro)))
        print("[校验] 原厂 psk 宏仍完好保留在非 K2P 分支 ✓")

    # 每个 `#ifdef BOARD_K2P` 必须紧跟 `#else` + `#endif`（三行结构，别漏 #endif 把整段吃掉）
    lines = src.split("\n")
    for i, l in enumerate(lines):
        if l.strip() == "#ifdef BOARD_K2P":
            nxt = [x.strip() for x in lines[i + 1: i + 6]]
            if "#else" not in nxt or "#endif" not in nxt:
                fail("预处理块不完整：第 %d 行 `#ifdef BOARD_K2P` 后面 5 行内找不到 #else/#endif" % (i + 1))
    print("[校验] %d 处 #ifdef BOARD_K2P 三行结构完整 ✓" % src.count("#ifdef BOARD_K2P"))
    print("[校验] 5G 默认发射功率仍为 100（未改动）✓")

    print()
    print("OK：K2P 出厂默认 = %s" % (", ".join(changed) if changed else "（本次无变化）"))
    if OPEN_DEFAULT_WIFI:
        print("OK：K2P 出厂 2.4G/5G = 开放网络（无密码）；其它机型仍是 psk + 1234567890")
    print("OK：其它 11AC 机型仍为 wl_HT_BW=2 / wl_wme=1，完全不受影响")
    print()
    print("提示：这只决定『没有该 nvram 项时写入的默认值』；")
    print("      设备上已手工设过的值保持手工值，不受影响。")


if __name__ == "__main__":
    main()

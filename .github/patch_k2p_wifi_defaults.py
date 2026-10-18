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
   注意：只改 5G 的 `wl_wme`；2.4G 的 `rt_wme` 见第 6 条。

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

6) `rt_wme`     : 1(开) -> 0(关)   【v2 新增：2.4G 对齐 QWRT】
   QWRT 主路由 2.4G 的 WMM 也是关的（WmmCapable=0，两频段一致）。
   现在 AP 的 2.4G 是回程 STA（连 QWRT 的「毛毛2.4g」），5G 上实测过的教训
   （wl_wme=1 → apcli0 关联失败 → 失联+看门狗重启）同样适用于 2.4G：
   STA 侧开着 WMM 去关联一个 WMM 关闭的 AP 是隐患。两边都关。

7) `rt_country_code` : 默认(DEF_WLAN_2G_CC) -> 固定 "CN"
   与 5G 的 wl_country_code 对齐，两频段恢复出厂都是 CN。

8) `rt_channel` : 0(auto) -> 11   【v3 新增：2.4G 信道默认钉 11】
   实测 AP1 的 2.4G 回程钉在 ch11（与主路由 2.4G 对齐、且避开邻里干扰）。
   恢复出厂即 ch11，不用再手工选。只影响 K2P。

9) `rt_HT_80211KV` : 1(开) -> 0(关)   【v4 新增：2.4G KV 关，对齐 QWRT】
   主路由 QWRT 2.4G 实测 k=0、v=0（K/V 关）。Padavan 出厂 rt_HT_80211KV=1。
   2.4G 只做回程、不服务客户端，KV 开关本无收益，且与上游对齐避免异常。
   注意只改 2.4G 的 rt_HT_80211KV；5G 的 wl_HT_80211KV 保持 1（5G 要 K/V 漫游）。

10) `wl_channel` : 0(auto) -> 149   【v5 新增：5G 信道默认钉 149】
   与 QWRT 主路由 rax 的 ch149 严格对齐。出厂 auto 会让 AP 开机时自行扫描选信道，
   一旦选到与主路由不同的信道，回程 apcli0 要重新扫描、客户端漫游也会掉一跳。
   钉死 149 后开机即在主路由信道上，回程关联更快、链路更稳。
   ⚠️ 5.8G 频段（149~165）在国内是开放频段，27dBm 功率不受限制；且 5.8G 比 5.2G 更安静。

11) `rt_TxPower` : 100 -> 20   【v6 新增：2.4G 功率默认 20%】
12) `wl_TxPower` : 100 -> 40   【v6 新增：5G 功率默认 40%】
   与主路由 QWRT 的 `def_txp=20 / 40` 完全一致（同一颗 MT76 驱动，txpower 是
   **0~100 的百分比**，不是 dBm：实测 2.4G 20% ≈ 21~24dBm、5G 40% ≈ 24dBm、100% = 27dBm）。
   三台机器都开满 27dBm 时覆盖重叠太狠，客户端根本没有换 AP 的理由 —— 这正是
   「漫游不灵敏」的第一号原因。统一降到 20/40 才能造出覆盖边界。

   ⚠️ 2.4G 是**回程**频段（apclix0 连主路由），功率压到 20% 会同时压低上行。
      实测 AP 与主路由距离在 5 米内时链路余量足够；若回程 RSSI 掉到 -70dBm 以下，
      把这一项改回 100（或只把 2.4G 改回 100、5G 保持 40）。

**K/V/R 双频段终局（与主路由 QWRT 完全一致）**：
  * 2.4G：`rt_HT_80211KV = 0`（K/V 关）+ `rt_HT_80211R = 0`（R 关）
  * 5G  ：`wl_HT_80211KV = 1`（K/V 开）+ `wl_HT_80211R = 0`（R 关）
  2.4G 只做回程、不服务客户端，K/V 开着没有任何收益；5G 才是客户端漫游的频段。
  R 关是因为 AP 是 Padavan/MTK、主路由是 QWRT，跨厂商 FT 不可靠，K/V 重关联已经够快。

   ⚠️ 以上全部只影响「恢复出厂 / 不保留配置刷机」之后的初始状态：
      * 「保留配置」刷机时 nvram 不动，手上已设好参数的机器**完全不受影响**；
      * 也**不影响**无线回程（apcli0）—— 上级 SSID/口令走 `wl_sta_ssid` /
        `wl_sta_wpa_psk`，那是另一组键。
   ⚠️ 出厂开放意味着「恢复出厂后被别人蹭网」的风险自负。不想要就把
      OPEN_DEFAULT_WIFI 改成 False（恢复成原厂的 psk + 预置口令）。

**没有改的项**（逐条比对过，Padavan 出厂默认已经与 AP 实测值一致）：
  wl_txbf=1、wl_ldpc=2、wl_mumimo=0、wl_HT_BAWinSize=256、wl_HT_AMSDU=1、
  wl_HT_AutoBA=1、wl_PktAggregate=1、wl_rts=2347、wl_frag=2346、wl_bcn=100、wl_dtim=1、
  wl_preamble=1、wl_stream_tx/rx=2/2、wl_gmode=4、wl_greenap=0。
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
TXPWR_LINE = '{ "wl_TxPower", "100" },'          # 5G 原厂默认 100
TXPWR24_LINE = '{ "rt_TxPower", "100" },'        # 2.4G 原厂默认 100


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


def pin_default(src, key, want, new_tail, label):
    """把某个 nvram 默认值**钉成** want（want 只会是 "0" / "1"）。

    三种情况：
      * 已经是 want      -> 跳过（幂等，不打第二遍补丁）
      * 是另一个布尔值   -> 用 #ifdef BOARD_K2P 三行结构改掉
      * 源码里没有这个键 -> 只告警、不中止（上游可能改过名字，让人去看一眼）
    """
    if re.search(r'(?m)^[ \t]*\{ "%s", "%s" \},' % (re.escape(key), want), src):
        print("[跳过] %s 已经是 %s" % (label, want))
        return src, False
    other = "0" if want == "1" else "1"
    if re.search(r'(?m)^[ \t]*\{ "%s", "%s" \},' % (re.escape(key), other), src):
        src, msg = wrap_k2p_any(src, '{ "%s", "%s" },' % (key, other),
                                '{ "%s", "%s" },' % (key, want), new_tail, label)
        print("[%s]" % msg)
        return src, True
    print("[警告] 源码里找不到 %s（既不是 %s 也不是 %s）—— 跳过，请人工核对"
          % (key, want, other))
    return src, False


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
    if TXPWR24_LINE not in src:
        fail("源码里找不到 %s —— 2.4G 默认功率不是 100，请人工确认后再编译" % TXPWR24_LINE)

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

    # ---------- 3b) rt_country_code (2.4G) ----------
    if PIN_COUNTRY_CN:
        if re.search(r'(?m)^[ \t]*\{ "rt_country_code", "CN" \},', src):
            print("[跳过] rt_country_code 已经是 CN")
        else:
            src, msg = wrap_k2p_any(
                src,
                '{ "rt_country_code", DEF_WLAN_2G_CC },',
                '{ "rt_country_code", "CN" },',
                "\t\t/* Country Code — K2P：固定 CN（与 5G 对齐） */",
                "rt_country_code(2.4G)",
            )
            print("[%s]" % msg)
            changed.append('rt_country_code=CN')
    else:
        print("[跳过] 按配置不钉 2.4G 国家码")

    # ---------- 3c) rt_wme (2.4G) ----------
    if re.search(r'(?m)^[ \t]*\{ "rt_wme", "0" \},', src):
        print("[跳过] rt_wme 已经是 0")
    else:
        src, msg = wrap_k2p_any(
            src,
            '{ "rt_wme", "1" },',
            '{ "rt_wme", "0" },',
            "\t\t\t/* WME mode — K2P：2.4G 对齐 QWRT（WMM 关），回程 STA 关联无隐患 */",
            "rt_wme(2.4G)",
        )
        print("[%s]" % msg)
        changed.append("rt_wme=0")

    # ---------- 3d) rt_channel (2.4G) ----------
    if re.search(r'(?m)^[ \t]*\{ "rt_channel", "11" \},', src):
        print("[跳过] rt_channel 已经是 11")
    else:
        src, msg = wrap_k2p_any(
            src,
            '{ "rt_channel", "0" },',
            '{ "rt_channel", "11" },',
            "\t\t/* Channel — K2P：2.4G 默认钉 11（对齐实测回程信道） */",
            "rt_channel(2.4G)",
        )
        print("[%s]" % msg)
        changed.append("rt_channel=11")

    # ---------- 3e) rt_HT_80211KV (2.4G KV) ----------
    if re.search(r'(?m)^[ \t]*\{ "rt_HT_80211KV", "0" \},', src):
        print("[跳过] rt_HT_80211KV 已经是 0")
    else:
        src, msg = wrap_k2p_any(
            src,
            '{ "rt_HT_80211KV", "1" },',
            '{ "rt_HT_80211KV", "0" },',
            "\t\t/* 802.11K/V — K2P：2.4G 只做回程，KV 关，对齐 QWRT */",
            "rt_HT_80211KV(2.4G)",
        )
        print("[%s]" % msg)
        changed.append("rt_HT_80211KV=0")

    # ---------- 3f) wl_channel (5G) ----------
    if re.search(r'(?m)^[ \t]*\{ "wl_channel", "149" \},', src):
        print("[跳过] wl_channel 已经是 149")
    else:
        src, msg = wrap_k2p(
            src,
            "wl_channel",
            '{ "wl_channel", "0" },',
            '{ "wl_channel", "149" },',
            "\t\t\t/* Channel — K2P：5G 默认钉 149（对齐 QWRT 主路由） */",
            "\t\t\t/* Channel number */",
        )
        print("[%s]" % msg)
        changed.append("wl_channel=149")

    # ---------- 3g) rt_TxPower (2.4G) ----------
    if re.search(r'(?m)^[ \t]*\{ "rt_TxPower", "20" \},', src):
        print("[跳过] rt_TxPower 已经是 20")
    else:
        src, msg = wrap_k2p_any(
            src,
            '{ "rt_TxPower", "100" },',
            '{ "rt_TxPower", "20" },',
            "\t\t\t/* TX power % — K2P：2.4G 默认 20%，与 QWRT 主路由一致 */",
            "rt_TxPower(2.4G)",
        )
        print("[%s]" % msg)
        changed.append("rt_TxPower=20")

    # ---------- 3h) wl_TxPower (5G) ----------
    if re.search(r'(?m)^[ \t]*\{ "wl_TxPower", "40" \},', src):
        print("[跳过] wl_TxPower 已经是 40")
    else:
        src, msg = wrap_k2p_any(
            src,
            '{ "wl_TxPower", "100" },',
            '{ "wl_TxPower", "40" },',
            "\t\t\t/* TX power % — K2P：5G 默认 40%，与 QWRT 主路由一致 */",
            "wl_TxPower(5G)",
        )
        print("[%s]" % msg)
        changed.append("wl_TxPower=40")

    # ---------- 3i) K/V/R 收口：2.4G = 0/0/0，5G = 1/1/0 ----------
    # 2.4G 的 K/V 已在 3e 改成 0；这里把剩下三个「应为」的值也钉住。
    # 原厂默认就是这些值，所以正常情况下全是「跳过」——它们是防止上游改默认值的护栏。
    for key, want, tail in (
        ('wl_HT_80211KV', "1", "\t\t/* 802.11K/V — K2P：5G 必须开，客户端漫游靠它 */"),
        ('rt_HT_80211R', "0", "\t\t/* 802.11R — K2P：2.4G 关（跨厂商 FT 不可靠） */"),
        ('wl_HT_80211R', "0", "\t\t/* 802.11R — K2P：5G 关（跨厂商 FT 不可靠） */"),
    ):
        src, did = pin_default(src, key, want, tail, key)
        if did:
            changed.append("%s=%s" % (key, want))

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

    if PIN_COUNTRY_CN and not re.search(r'(?m)^[ \t]*\{ "rt_country_code", "CN" \},', src):
        fail("校验失败：找不到 K2P 的 rt_country_code=CN")

    if re.search(r'(?m)^[ \t]*\{ "rt_wme", "0" \},', src):
        print("[校验] rt_wme  -> 0（K2P 分支）✓")
    else:
        fail("校验失败：找不到 K2P 的 rt_wme=0")

    if re.search(r'(?m)^[ \t]*\{ "rt_channel", "11" \},', src):
        print("[校验] rt_channel  -> 11（K2P 分支）✓")
    else:
        fail("校验失败：找不到 K2P 的 rt_channel=11")

    if re.search(r'(?m)^[ \t]*\{ "rt_HT_80211KV", "0" \},', src):
        print("[校验] rt_HT_80211KV  -> 0（K2P 分支）✓")
    else:
        fail("校验失败：找不到 K2P 的 rt_HT_80211KV=0")

    if re.search(r'(?m)^[ \t]*\{ "wl_channel", "149" \},', src):
        print("[校验] wl_channel  -> 149（K2P 分支）✓")
    else:
        fail("校验失败：找不到 K2P 的 wl_channel=149")

    if re.search(r'(?m)^[ \t]*\{ "rt_TxPower", "20" \},', src):
        print("[校验] rt_TxPower  -> 20（K2P 分支）✓")
    else:
        fail("校验失败：找不到 K2P 的 rt_TxPower=20")

    if re.search(r'(?m)^[ \t]*\{ "wl_TxPower", "40" \},', src):
        print("[校验] wl_TxPower  -> 40（K2P 分支）✓")
    else:
        fail("校验失败：找不到 K2P 的 wl_TxPower=40")

    # K/V/R 终局：2.4G 0/0/0，5G 1/1/0
    for key, want in (('rt_HT_80211KV', "0"), ('wl_HT_80211KV', "1"),
                      ('rt_HT_80211R', "0"), ('wl_HT_80211R', "0")):
        if not re.search(r'(?m)^[ \t]*\{ "%s", "%s" \},' % (key, want), src):
            fail("校验失败：找不到 %s=%s" % (key, want))
    print("[校验] K/V/R：2.4G = 0/0/0、5G = 1/1/0 ✓")

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
    print("[校验] 双频段默认功率已钉 20/40（原厂 100/100）✓")

    print()
    print("OK：K2P 出厂默认 = %s" % (", ".join(changed) if changed else "（本次无变化）"))
    print("OK：2.4G 信道 11 / 功率 20 / K-V-R 全关；5G 信道 149 / 功率 40 / K-V 开、R 关")
    if OPEN_DEFAULT_WIFI:
        print("OK：K2P 出厂 2.4G/5G = 开放网络（无密码）；其它机型仍是 psk + 1234567890")
    print("OK：其它 11AC 机型仍为 wl_HT_BW=2 / wl_wme=1，完全不受影响")
    print()
    print("提示：这只决定『没有该 nvram 项时写入的默认值』；")
    print("      设备上已手工设过的值保持手工值，不受影响。")


if __name__ == "__main__":
    main()

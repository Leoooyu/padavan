#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在 Padavan 源码树里给 mtd_storage.sh 打补丁（供 GitHub Actions 调用）。

做两件事：
  1) 定义 func_tsl_fixes()：
     - 写入 /etc/storage/ap_watchdog.sh（**通用版**看门狗：探测目标 = 本机实际默认网关，
       默认每 5 分钟一次，一次不通即重启；带 MIN_UPTIME 护栏防打转）
       * 脚本头带 `watchdog-version: v4-generic` 标记，旧版会自动升级
       * 重启逻辑移植自 QWRT：graceful = 优雅重启 + 6 秒 sysrq 兜底（默认）
     - 写入 /etc/storage/reboot-hard.sh（QWRT 的 /usr/bin/reboot-hard：内核级紧急重启）
     - 写入 /etc/storage/reboot-safe.sh（QWRT 的 /sbin/reboot 包装器：优雅优先 + 兜底）
     - 给 started_script.sh 追加启动钩子（pid 文件 + start-stop-daemon，不依赖 cron）
     - 给 started_script.sh 追加 IPv6 组播修复段（br0 snooping=0 + 各口 disable_ipv6=0）
     - 给 started_script.sh 追加 IPv6 DAD 探测拦截段（无线中继 / MAC 转换下防 dadfailed）
     - 给 shutdown_script.sh 追加 sysrq 兜底（正常关机优先，卡住 6 秒强制重启）
  2) 在 case "$1" 的 load) / reset) / fill) 分支里调用这个块，保证
     - 首次开机（fill/reset）就写进去
     - 每次开机（load）都会补齐缺失项（幂等：grep / 版本标记守卫）

追加内容全部是"只新增、不改动原有逻辑"，安全可回滚（同目录留 .orig 备份）。
"""
import os
import re
import sys

MARK = "# ===== tsl baked-in fixes (watchdog + reliable reboot) ====="

# FIXME: 下面这个 BLOCK 是一整段 shell（含内嵌 heredoc），原样保留，不要格式化。
BLOCK = r'''
# ===== tsl baked-in fixes (watchdog + reliable reboot) =====
# 通用版桥接看门狗 + QWRT 式重启（优雅优先 + sysrq 兜底）。只新增文件/追加内容，不改动原有逻辑。
func_tsl_fixes()
{
	WD="/etc/storage/ap_watchdog.sh"
	# 带版本标记：缺失或旧版时自动写入/升级；已是 v4 就不动
	if [ ! -f "$WD" ] || ! grep -q "watchdog-version: v4-generic" "$WD" ; then
		cat > "$WD" <<'TSLEOF'
#!/bin/sh
# ================= 桥接看门狗（通用版）=================
# 上级路由失联 -> 重启本机。由 install-ap-fixes.py 安装。
# watchdog-version: v4-generic
#
# 重启逻辑移植自之前的 QWRT 固件（MTK 平台正常关机流程可能卡死）：
#   graceful = 优雅重启优先，同时武装一个内核级兜底定时器
#              （对应 QWRT 的 /sbin/reboot 包装器）
#   hard     = 直接内核级 sysrq 重启（对应 QWRT 的 /usr/bin/reboot-hard）
#
# 可调参数（只改这一段即可）：
GW_OVERRIDE=""	# 留空 = 自动取本机默认网关；也可写死，如 "192.168.2.1"
INTERVAL=300		# 检查间隔（秒）：300 = 5 分钟，600 = 10 分钟
MISS_LIMIT=1		# 连续几轮不通就重启：1 = 一次不通就重启
PING_TRIES=3		# 一轮内最多试几次（每次 2 个包、2 秒超时）
SOFT_FIRST=0		# 1 = 重启前先试软恢复（重启无线 + 补挂网桥，多花约 30 秒）
MIN_UPTIME=300		# 本机启动不足这么多秒时，不通也不重启（防打转）
REBOOT_MODE="graceful"	# graceful = 优雅重启 + sysrq 兜底；hard = 直接 sysrq（QWRT 的 reboot-hard）
SYSRQ_GRACE=6		# graceful：等这么多秒还没关机就发内核级重启（与 QWRT 一致）
# ======================================================

CNTF=/tmp/ap_watchdog.cnt
TAG=apwd

log() { logger -t $TAG "$*" ; }

# 取探测目标：优先本机实际默认网关（默认路由）
get_gw() {
	if [ -n "$GW_OVERRIDE" ] ; then
		echo "$GW_OVERRIDE"
		return
	fi
	g=$(ip route 2>/dev/null | awk '/^default/ {print $3; exit}')
	[ -n "$g" ] || g=$(route -n 2>/dev/null | awk '$1=="0.0.0.0" {print $2; exit}')
	echo "$g"
}

uptime_sec() { awk '{print int($1)}' /proc/uptime 2>/dev/null ; }

# 一轮内多试几次，避免"丢一个包"就判失联
alive() {
	i=1
	while [ "$i" -le "$PING_TRIES" ] ; do
		ping -c 2 -W 2 "$1" >/dev/null 2>&1 && return 0
		i=$((i + 1))
	done
	return 1
}

# 软恢复：重启 5G 无线（桥接用的 apcli0 在 5G 上），再补挂一次网桥
soft_recover() {
	[ -x /sbin/radio2_restart ] && /sbin/radio2_restart
	sleep 20
	for i in apcli0 ra0 rax0 ; do
		[ -e "/sys/class/net/$i" ] && brctl addif br0 "$i" 2>/dev/null
	done
	log "soft wireless restart done"
}

# 内核级紧急重启（对应 QWRT 的 /usr/bin/reboot-hard）：
# 跳过 init / 服务关闭 / 驱动卸载，是本机唯一一定生效的重启方式。
# 先 's' 同步一次、留 1 秒回写，再 'b' 立即重启（与 QWRT 的写法一致）。
sysrq_reboot() {
	echo 1 > /proc/sys/kernel/sysrq
	echo s > /proc/sysrq-trigger
	sleep 1
	echo b > /proc/sysrq-trigger
}

# 重启入口（对应 QWRT 的 /sbin/reboot 包装器）：
# 优雅重启优先，**同一时刻**武装一个 SYSRQ_GRACE 秒的内核级兜底定时器。
# 这样即使关机流程卡死、或 reboot 静默失败，定时器也会把机器真正重启掉。
do_reboot() {
	sync
	case "$REBOOT_MODE" in
		hard|sysrq)
			log "REBOOT: hard (sysrq, skip init)"
			sysrq_reboot
			;;
		*)
			log "REBOOT: graceful, sysrq fallback in ${SYSRQ_GRACE}s"
			# trap 必须忽略 TERM/INT/HUP：/sbin/rc 的关机流程走完后，init 会执行
			# kill(-1, SIGTERM)（向所有进程广播），不忽略的话这个后台兜底会被一起杀掉
			# —— 真机实测过：无 trap 的 subshell 收到 TERM 后，后面的命令一条都不执行。
			# 延时用"等绝对时刻"而不是裸 sleep：kill(-1) 会连 sleep 子进程一起收掉，
			# 光靠 sleep 会立刻返回、导致兜底提前触发。
			(
				trap '' TERM INT HUP
				_d=$(( $(date +%s) + SYSRQ_GRACE ))
				while [ "$(date +%s)" -lt "$_d" ] ; do sleep 1 2>/dev/null ; done
				sysrq_reboot
			) &
			reboot
			;;
	esac
}

check_once() {
	gw=$(get_gw)
	if [ -z "$gw" ] ; then
		# 取不到网关（例如刚开机还没起路由）时不算失联，避免误重启
		log "no default gateway -> skip this round"
		return 0
	fi

	if alive "$gw" ; then
		[ -f "$CNTF" ] && rm -f "$CNTF"
		return 0
	fi

	n=$(cat "$CNTF" 2>/dev/null)
	[ -z "$n" ] && n=0
	n=$((n + 1))
	echo "$n" > "$CNTF"
	log "uplink $gw unreachable (round $n/$MISS_LIMIT)"

	if [ "$n" -lt "$MISS_LIMIT" ] ; then
		soft_recover
		return 0
	fi

	if [ "$SOFT_FIRST" = "1" ] ; then
		soft_recover
		if alive "$gw" ; then
			log "soft recovery worked -> no reboot"
			rm -f "$CNTF"
			return 0
		fi
		log "soft recovery failed"
	fi

	up=$(uptime_sec)
	if [ -n "$up" ] && [ "$up" -lt "$MIN_UPTIME" ] ; then
		log "uptime ${up}s < ${MIN_UPTIME}s -> skip reboot (防打转)"
		rm -f "$CNTF"
		return 0
	fi

	rm -f "$CNTF"
	log "uplink $gw down -> rebooting AP now"
	do_reboot
	return 0
}

case "$1" in
	--gw)	get_gw ; exit 0 ;;
	--once)	check_once ; echo "checked once (gw=$(get_gw))" ; exit 0 ;;
	--reboot)	do_reboot ; exit 0 ;;
	*)
		# 只在 AP / 中继模式（sw_mode=2/3）跑主循环。
		# 路由器 / 网关模式（sw_mode=1/4）的默认路由指向 WAN，
		# WAN 抖动会被误判成「上游失联」并重启整机 —— 必须跳过。
		case "$(nvram get sw_mode)" in
			2|3) ;;
			*)	log "sw_mode=$(nvram get sw_mode) (router/gateway) -> watchdog disabled" ; exit 0 ;;
		esac
		while true ; do
			check_once
			sleep "$INTERVAL"
		done ;;
esac
TSLEOF
		chmod 755 "$WD"
	fi
	HB="/etc/storage/reboot-hard.sh"
	if [ ! -f "$HB" ] || ! grep -q "qwrt-reboot-hard: v1" "$HB" ; then
		cat > "$HB" <<'TSLEOF'
#!/bin/sh
# ================= 内核级紧急重启 =================
# qwrt-reboot-hard: v1   （移植自 QWRT 固件的 /usr/bin/reboot-hard）
# 跳过 init、服务关闭、驱动卸载 —— 本机（MTK）正常关机流程可能卡住，
# 卡住时只有这个能真正重启。
# 用法: sh /etc/storage/reboot-hard.sh
sync
echo 1 > /proc/sys/kernel/sysrq
echo s > /proc/sysrq-trigger
sleep 1
echo b > /proc/sysrq-trigger
TSLEOF
		chmod 755 "$HB"
	fi
	SB="/etc/storage/reboot-safe.sh"
	if [ ! -f "$SB" ] || ! grep -q "qwrt-reboot-safe: v2" "$SB" ; then
		cat > "$SB" <<'TSLEOF'
#!/bin/sh
# ================= 优雅重启 + 自动兜底 =================
# qwrt-reboot-safe: v2   （移植自 QWRT 固件的 /sbin/reboot 包装器）
# 先走正常关机；GRACE 秒后机器还活着（关机流程卡住 / reboot 没生效），
# 自动转内核级强制重启 —— 保证"重启一定发生"。
# Padavan 的 / 是只读 squashfs，换不掉 /sbin/reboot，所以放在这里。
# v2 加固：兜底 subshell 忽略 TERM/INT/HUP —— 关机流程里 init 会 kill(-1, SIGTERM)，
#          不忽略就会被一起杀掉；延时改"等绝对时刻"，避免 sleep 被连带杀死后提前触发。
# 用法: sh /etc/storage/reboot-safe.sh
GRACE=6
sync
(
	trap '' TERM INT HUP
	_d=$(( $(date +%s) + GRACE ))
	while [ "$(date +%s)" -lt "$_d" ] ; do sleep 1 2>/dev/null ; done
	echo 1 > /proc/sys/kernel/sysrq
	echo s > /proc/sysrq-trigger
	echo b > /proc/sysrq-trigger
) &
reboot
TSLEOF
		chmod 755 "$SB"
	fi
	# ---- 启动钩子（started_script.sh）----
	# 文件不存在就先按官方模板补齐。官方是在 func_fill() 里生成的，而 load) 分支
	# 只调 func_load()（纯解包，不补文件）—— 极端情况下（storage 被手动清过）
	# 钩子会被静默跳过，所以这里自己兜一层。
	ST="/etc/storage/started_script.sh"
	if [ ! -f "$ST" ] ; then
		printf '#!/bin/sh\n\n### Custom user script\n### Called after router started and network is ready\n\n' > "$ST"
		chmod 755 "$ST"
	fi
	if ! grep -q ap_watchdog "$ST" ; then
		cat >> "$ST" <<'TSLEOF'

# ==== AP 桥接看门狗（由安装脚本写入；本体见 /etc/storage/ap_watchdog.sh）====
# Padavan 没有 setsid：用 pid 文件 + start-stop-daemon 脱离会话
# （实测直接 `( ... ) &` 起的后台任务会随 SSH 会话被回收）。
# 守卫用 kill -0 探测 pid —— busybox 的 `ps w` 会截断过长命令行，grep 守卫会误判。
# 模式门控不在这里做：交给 ap_watchdog.sh 自己判断（路由/网关模式会立即 exit 0），
# 避免在启动早期调用 nvram 导致死锁。
PIDF=/var/run/ap_watchdog.pid
if [ ! -s "$PIDF" ] || ! kill -0 "$(cat "$PIDF" 2>/dev/null)" 2>/dev/null ; then
	start-stop-daemon -S -b -m -p "$PIDF" -x /bin/sh -- -c '/etc/storage/ap_watchdog.sh'
fi
TSLEOF
	fi
	# ---- IPv6 组播修复 ----
	# 两项都是**运行时 sysfs**（重启回内核默认），所以必须放在启动钩子里每次重放：
	#  1) br0 桥的 IGMP/MLD snooping 默认 1。本方案回程是「2.4G 无线 STA」(apclix0 桥进 br0)，
	#     无线链路的 IGMP/MLD 查询本来就容易丢；桥的组播成员表一老化，就会**停止把组播
	#     转发给 5G 客户端** -> 老设备连 5G 拿不到 IPv6（RA ff02::1 到不了），
	#     AirPlay/Chromecast 时好时坏。实机踩过。
	#  2) 内核默认给非 br-lan 端口 disable_ipv6=1，同样挡住 RA。
	# ⚠️ 别指望 nvram 的 wl_IgmpSnEnable / rt_IgmpSnEnable：K2P 这个编译配置下它不生效
	#    （net_wifi.c 的 brport_set_m2u 被 USE_IGMP_SNOOP 编译掉了；实测
	#      ra0/brport/multicast_to_unicast 恒为 0，改 nvram 纹丝不动）。实测结论，别再走弯路。
	if ! grep -q "ipv6-mcast-fix: v1" "$ST" ; then
		cat >> "$ST" <<'TSLEOF'

# >>> ipv6-mcast-fix: v1 >>>
# IPv6 组播（RA / mDNS）修复 —— 两项都是运行时 sysfs，重启即回默认，必须每次启动重放。
#
# 1) 关 br0 内核桥的 IGMP/MLD snooping（内核默认 =1）。
#    本方案回程是「2.4G 无线 STA」(apclix0 桥进 br0)，无线链路上 IGMP/MLD 查询容易丢；
#    桥的组播成员表一老化，它就会停止把组播帧转发给 5G 客户端 ——
#    表现：老设备（Android 盒子等）连 5G 拿不到 IPv6，AirPlay/Chromecast 时好时坏。
#    实测 nvram 的 wl_IgmpSnEnable / rt_IgmpSnEnable 在本编译配置下不生效
#    （net_wifi.c 的 brport_set_m2u 被 USE_IGMP_SNOOP 编译掉了；
#     实测 ra0/brport/multicast_to_unicast 恒为 0，改 nvram 纹丝不动），只能直接写 sysfs。
#
# 2) 放开 ra0/rax0/apclix0/br0 的 disable_ipv6（内核默认给非 br-lan 端口置 1，RA 进不来）。
for _i in 1 2 3 4 5 6 7 8 9 10 ; do [ -e /sys/class/net/ra0 ] && break ; sleep 1 ; done
echo 0 > /sys/class/net/br0/bridge/multicast_snooping 2>/dev/null
for _if in ra0 rax0 apclix0 br0 ; do
	echo 0 > /proc/sys/net/ipv6/conf/$_if/disable_ipv6 2>/dev/null
done
# <<< ipv6-mcast-fix <<<
TSLEOF
	fi
	# ---- IPv6 DAD 探测拦截（无线中继 / MAC 转换专属问题）----
	# 3-address 无线中继（apcli STA 回程）会把客户端的源 MAC 改写成 AP 的代理 MAC。
	# 客户端做 DAD 时发的 NS（源地址 ::，目的 solicited-node 组播）会被桥的组播泛洪绕回来，
	# 而源 MAC 已经不是它自己 -> 客户端内核判定「另一节点也在用这个地址」-> 该地址 dadfailed。
	# 用 EUI-64 固定地址的设备（电视盒子 / 智能电视）重试还是同一个地址，永远逃不掉；
	# 用隐私地址的手机每次重试换 IID，所以看不出问题 —— 这就是「电视全没 IPv6、手机正常」的成因。
	# 处理：在无线口入口丢弃 DAD 探测。不发就不会撞车，内核走「DAD 超时」路径正常启用地址。
	# 只匹配「源地址 :: 的 NS」= RFC 4862 规定的 DAD 唯一形式
	#   -> RA / 正常 ND / mDNS / SSDP / DLNA 零影响，这也是它比「关组播」安全的原因
	#      （关组播会连 RA ff02::1 一起杀掉，等于关掉整个 IPv6）。
	# 代价：无线客户端失去 DAD 冲突检测（静态配成重复地址会静默冲突），家庭网络可接受。
	# 只在 AP / 中继模式（sw_mode=2/3）启用；不需要时 `nvram set ipv6_dad_guard=0; nvram commit`。
	if ! grep -q "ipv6-dad-guard: v1" "$ST" ; then
		cat >> "$ST" <<'TSLEOF'

# >>> ipv6-dad-guard: v1 >>>
# 无线中继(MAC 转换)下，客户端自己发的 DAD 探测会被桥的组播泛洪绕回来，
# 源 MAC 已变成 AP 的代理 MAC -> 客户端内核判定地址冲突 -> dadfailed -> 该地址永久不可用。
# 这里把无线口的 DAD 探测（ICMPv6 NS，源地址 ::）在入口丢弃：不发就不撞车，
# 内核走「DAD 超时」路径正常启用地址（多约 1 秒）。
# 只匹配 RFC 4862 规定的 DAD 唯一形式，RA / 正常 ND / mDNS / SSDP 完全不受影响。
# 只在 AP / 中继模式(sw_mode=2/3)生效；关掉：nvram set ipv6_dad_guard=0; nvram commit
# 注意：本段在 rc 启动流程里被执行，不可提前终止流程（不能掐断 rc）。
# ⚠️ 判 ebtables 是否存在只能看路径，**不要用 `command -v`**：
#    本机 /bin/sh 是 busybox，`command -v` 返回 127（不支持），整段会被静默跳过（实测踩过）。
case "$(nvram get sw_mode)" in
	2|3)
		if [ "$(nvram get ipv6_dad_guard)" != "0" ] && { [ -x /bin/ebtables ] || [ -x /usr/sbin/ebtables ] || [ -x /sbin/ebtables ] ; } ; then
			for _i in 1 2 3 4 5 6 7 8 9 10 ; do [ -e /sys/class/net/ra0 ] && break ; sleep 1 ; done
			# 自定义链：每次重建，天然幂等，反复执行也不会堆积规则
			ebtables -N DADGUARD 2>/dev/null
			ebtables -F DADGUARD 2>/dev/null
			for _if in ra0 rax0 ra1 rax1 ; do
				[ -e "/sys/class/net/$_if" ] || continue
				ebtables -A DADGUARD -p IPv6 --ip6-proto ipv6-icmp --ip6-icmp-type 135 --ip6-src ::/128 -i "$_if" -j DROP 2>/dev/null
				ebtables -A DADGUARD -p IPv6 --ip6-proto ipv6-icmp --ip6-icmp-type 135 --ip6-src ::/128 -o "$_if" -j DROP 2>/dev/null
			done
			for _k in 1 2 3 ; do ebtables -D FORWARD -j DADGUARD 2>/dev/null || break ; done
			ebtables -I FORWARD 1 -j DADGUARD 2>/dev/null || ebtables -A FORWARD -j DADGUARD 2>/dev/null
			logger -t dadguard "wireless IPv6 DAD probes dropped"
		fi
		;;
esac
# <<< ipv6-dad-guard <<<
TSLEOF
	fi
	# ---- 关机兜底（v2 加固）----
	# 这是**所有**重启路径的最后一道保险：手动重启、网页重启、看门狗重启，
	# 以及「高级设置 > 系统管理 > 其他设置」里那个**自带定时重启**
	# （watchdog.c 的 reboot_schedule -> sys_exit() -> kill(1,SIGTERM)
	#   -> init 关机 -> 执行本脚本），全都要经过这里。
	# 所以把它做扎实 = 把整条重启链做扎实。
	# v1 会被 init 随后的 kill(-1, SIGTERM) 干掉，v2 起用 trap 免疫。
	SH="/etc/storage/shutdown_script.sh"
	# 文件不存在就先按官方模板补齐（同 started_script.sh 的理由）。
	if [ ! -f "$SH" ] ; then
		cat > "$SH" <<'TSLEOF'
#!/bin/sh

### Custom user script
### Called before router shutdown
### $1 - action (0: reboot, 1: halt, 2: power-off)

TSLEOF
		chmod 755 "$SH"
	fi
	if ! grep -q "sysrq-fallback: v3" "$SH" ; then
		# 先摘掉历史版本（v1/v2/更早的三秒版都可能残留），再重写。
		# 判据**只用 ASCII**：先找含 `sysrq-trigger` 的那行，向上回溯到最近的 `if` 行
		# 及其紧邻的注释行 = 块首；向下找第一个独立的 `fi` 行 = 块尾（v2 还要吃掉
		# 紧随其后的 `# <<< sysrq-fallback <<<` 结束标记）。
		# 为什么要缓冲整个文件：需要"向前看"再整体重排，单遍 awk 做不到删块。
		# 为什么不用中文正则（如 /^# reboot 兜底/）：虽然实测 busybox awk 认得，
		# 但 CJK 匹配依赖 locale/编码，换个 busybox 就可能失效 —— ASCII 判据无此风险。
		awk '
			{ L[NR] = $0 ; F[NR] = $1 ; N[NR] = NF }
			END {
				k = 0
				for (i = 1; i <= NR; i++)
					if (index(L[i], "sysrq-trigger")) { k = i ; break }
				if (!k) { for (i = 1; i <= NR; i++) print L[i] ; exit }
				# 块首：从 k 往上找最近的 if 行，再吃掉紧邻的注释行
				s = k
				for (i = k; i >= 1; i--)
					if (F[i] == "if") { s = i ; break }
				# 注释行判据用 L[]（整行）而不是 F[]：块里有"只有 # 一行"的空注释，
				# 那种行 NF=0、$1 为空，用字段判断会提前停下、导致块头残留。
				# 用 POSIX 字符类 [[:space:]] 而不是 \\t —— \\t 在方括号里不可移植。
				while (s > 1 && L[s-1] ~ /^[[:space:]]*#/) s--
				# 块尾：从 k 往下找第一个"整行只有 fi"
				e = NR
				for (i = k; i <= NR; i++)
					if (F[i] == "fi" && N[i] == 1) { e = i ; break }
				# v2 的结束标记要跟着块一起删，否则每次升级都多留一行
				if (e < NR && L[e+1] ~ /^[[:space:]]*#/ && index(L[e+1], "sysrq-fallback")) e++
				for (i = 1; i < s; i++) print L[i]
				for (i = e + 1; i <= NR; i++) print L[i]
			}
		' "$SH" > "/tmp/_sh_new.$$" \
			&& [ -s "/tmp/_sh_new.$$" ] && cat "/tmp/_sh_new.$$" > "$SH" && rm -f "/tmp/_sh_new.$$"
		cat >> "$SH" <<'TSLEOF'

# >>> sysrq-fallback: v3 >>>
# reboot 兜底（由固件/脚本安装）：正常关机优先，卡住 6 秒后用内核 sysrq 强制重启。
# 只对「重启」动作生效（$1=0），停机和关电不会误触发。
#
# v3 关键修复：网页升级（rc.c 的 flash_firmware）也会以 $1=0 调用本脚本，
#   但随后 mtd_write 还要写整个固件（30~60 秒）。若 6 秒兜底此时触发 sysrq-b，
#   会打断写入、把固件写坏 -> 启动失败 -> 落 Breed（实机踩过：AP1 反复蓝灯闪）。
#   所以检测到「待刷固件」（httpd 上传到 /tmp/linux.trx，>1MB）就跳过兜底。
#
# 为什么必须 trap：init 跑完关机脚本后会执行 kill(-1, SIGTERM) 广播 SIGTERM，
# 不加 trap 的话这个后台兜底会被一起杀掉、等于没装（真机实测：无 trap 的 subshell
# 收到 TERM 后，后面的命令一条都不执行）。trap 之后 init 就杀不动它了。
# 为什么用时间戳：kill(-1) 会把 sleep 子进程一起收掉，裸 sleep 会立刻返回、
# 导致兜底提前触发；改成"等一个绝对时刻"，无论中途被打断几次都保证等够 6 秒。
if [ "$1" = "0" ] ; then
	_sz=0
	[ -f /tmp/linux.trx ] && _sz=$(wc -c < /tmp/linux.trx 2>/dev/null)
	[ "${_sz:-0}" -gt 1000000 ] || {
		sync
		(
			trap '' TERM INT HUP
			_end=$(( $(date +%s) + 6 ))
			while [ "$(date +%s)" -lt "$_end" ] ; do sleep 1 2>/dev/null ; done
			echo 1 > /proc/sys/kernel/sysrq
			echo s > /proc/sysrq-trigger
			echo b > /proc/sysrq-trigger
		) &
	}
fi
# <<< sysrq-fallback <<<
TSLEOF
		chmod 755 "$SH"
	fi
	# 立刻起一次（不等下次重启）：pid 文件 + start-stop-daemon（本机无 setsid，& 会随会话被回收）
	# ⚠️ 这里绝对不能调用 nvram：本函数挂在 fill)/load) 分支，fill 阶段 nvram 尚未就绪，
	#    `nvram get` 会死锁、导致整机启动卡死（实机踩过）。
	#    模式门控交给 ap_watchdog.sh 自己判断（运行时 nvram 可用，路由/网关模式会立即 exit 0）。
	if [ ! -s "/var/run/ap_watchdog.pid" ] || ! kill -0 "$(cat /var/run/ap_watchdog.pid 2>/dev/null)" 2>/dev/null ; then
		start-stop-daemon -S -b -m -p "/var/run/ap_watchdog.pid" -x /bin/sh -- -c '/etc/storage/ap_watchdog.sh'
	fi
}
# ===== end of tsl baked-in fixes =====
'''


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        # 自己找
        for root, _dirs, files in os.walk("trunk"):
            for f in files:
                if f.endswith("mtd_storage.sh"):
                    path = os.path.join(root, f)
                    break
            if path:
                break
    if not path or not os.path.isfile(path):
        print("找不到 mtd_storage.sh")
        return 1
    print("打补丁:", path)
    src = open(path, encoding="utf-8", errors="surrogateescape").read()
    if MARK in src:
        print("已经打过补丁，跳过")
        return 0
    orig = src

    # 1) 插入函数定义（放在 func_fill 之前，确保被调用时已定义）
    m = re.search(r"(?m)^func_fill\(\)", src)
    if not m:
        print("找不到 func_fill()，放弃")
        return 1
    src = src[:m.start()] + BLOCK.strip("\n") + "\n\n" + src[m.start():]

    # 2) 在 case 的 load) / reset) / fill) 分支里调用
    #    load) 里跟在 func_load 后面；reset)/fill) 里跟在 func_fill 后面
    src = src.replace("\tfunc_load\n", "\tfunc_load\n\tfunc_tsl_fixes\n", 1)
    src = src.replace("\tfunc_fill\n", "\tfunc_fill\n\tfunc_tsl_fixes\n")
    if "func_tsl_fixes" not in src.split('case "$1" in', 1)[-1]:
        print("警告：没有挂到 case 分支上，请手工检查")
        return 1

    # newline="" -> 写文件时不做任何换行转换，保证与源码树行尾一致（上游是 LF）。
    # 若不写，在 Windows 上跑补丁会把整个文件转成 CRLF：shell 脚本每行尾带 \r，
    # 真机上会出现 `#!/bin/sh\r` 之类的怪错误（本地实测过 722 行 → 944 行全变 CRLF）。
    open(path + ".orig", "w", encoding="utf-8", errors="surrogateescape", newline="").write(orig)
    open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(src)
    print("已写入（原文件备份为 %s.orig）" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

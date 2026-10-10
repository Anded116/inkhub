#!/bin/sh
# Диагностика Kindle Clock: сеть, связь с ПК, нужные команды. Результат в diag.txt.
# Запуск: Shift, C, D. Интерфейс Kindle при этом не замораживается.

APP_DIR="/mnt/us/kindleclock"
OUT="$APP_DIR/diag.txt"
LUA="/var/tmp/kindleclock-diag-luajit"
HUB_URL=""
[ -f "$APP_DIR/config.sh" ] && . "$APP_DIR/config.sh"

limit() {
	lm_t=$1
	shift
	"$@" &
	lm_pid=$!
	(sleep "$lm_t"; kill "$lm_pid" 2>/dev/null) >/dev/null 2>&1 </dev/null &
	lm_w=$!
	wait "$lm_pid"
	lm_rc=$?
	kill "$lm_w" 2>/dev/null
	echo "[код выхода $lm_rc]"
}

hub_host=${HUB_URL#http://}
hub_host=${hub_host%%/*}
hub_host=${hub_host%%:*}
cp -f "$APP_DIR/bin/luajit" "$LUA" && chmod 755 "$LUA"

{
	echo "== $(date)"
	uname -a
	busybox 2>&1 | head -n 1
	for c in wget nc gunzip od hwclock ping ifconfig iptables; do
		printf '%s: ' "$c"
		which "$c" 2>/dev/null || echo "нет"
	done

	echo
	echo "== Wi-Fi"
	echo "cmState: $(lipc-get-prop com.lab126.wifid cmState 2>&1)"
	echo "wpa_cli:"
	wpa_cli -i wlan0 status 2>&1
	echo "iwconfig:"
	iwconfig wlan0 2>&1
	echo "signal: $(lipc-get-prop com.lab126.wifid signalStrength 2>&1)"
	ifconfig 2>&1
	route -n 2>&1
	cat /etc/resolv.conf 2>&1
	echo "ARP:"
	cat /proc/net/arp 2>&1

	echo
	echo "== ПК ($HUB_URL) через kctool"
	rm -f /var/tmp/kindleclock-diag-ping
	limit 15 "$LUA" "$APP_DIR/bin/kctool.lua" get "$HUB_URL/api/ping" /var/tmp/kindleclock-diag-ping 10 2>&1
	cat /var/tmp/kindleclock-diag-ping 2>/dev/null

	echo
	echo "== ПК через wget"
	limit 15 wget -O - "$HUB_URL/api/ping" 2>&1

	echo
	echo "== ping ПК ($hub_host)"
	limit 10 ping -c 3 "$hub_host" 2>&1

	echo
	echo "== интернет через kctool"
	limit 15 "$LUA" "$APP_DIR/bin/kctool.lua" headers http://www.google.com/ 10 2>&1 | head -n 6

	echo
	echo "== интернет через wget"
	limit 15 wget -S -O /dev/null http://www.google.com/ 2>&1 | head -n 8

	echo
	echo "== звук (для будущего плеера)"
	cat /proc/asound/cards 2>&1
	ls -l /dev/snd /dev/dsp* /dev/audio* 2>&1
	for d in /bin /sbin /usr/bin /usr/sbin /usr/local/bin; do
		ls "$d" 2>/dev/null | grep -i -E 'play|mp3|mad|mpg|ogg|gst|alsa|amixer|audio|sound|media|mixer' | sed "s#^#$d/#"
	done
	ls /usr/lib /lib 2>/dev/null | grep -i -E 'mad|mpg|mp3|ogg|vorbis|flac|gst|asound|audio|sound' | sort -u
	ls /usr/java/lib 2>/dev/null | grep -i -E 'audio|sound|media|mp3' 
	lipc-probe -a 2>/dev/null | grep -i -E 'audio|media|music|player|sound' | head -n 30
	ls /mnt/us/music 2>&1 | head -n 10
	echo
	echo "== брандмауэр Kindle"
	iptables -L -n 2>&1 | head -n 40
} >"$OUT" 2>&1

rm -f "$LUA" /var/tmp/kindleclock-diag-ping
eips 1 38 "Kindle Clock: diag.txt ready" >/dev/null 2>&1

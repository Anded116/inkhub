#!/bin/sh
# Kindle Clock: настольные часы и инфопанель для Kindle 3 (Kindle Keyboard).
# Запуск: sh /mnt/us/kindleclock/clock.sh. Выход: Home или Back.

APP_DIR="/mnt/us/kindleclock"
RUN_DIR="/var/tmp/kindleclock"
PID_FILE="/var/tmp/kindleclock.pid"
FIFO="$RUN_DIR/events"
BUSY="$RUN_DIR/busy"
LOG="$APP_DIR/clock.log"
STATE="$APP_DIR/state.sh"
PACK_DIR="$APP_DIR/pack"
FBINK="$RUN_DIR/fbink"
LUAJIT="$RUN_DIR/luajit"
KCTOOL="$APP_DIR/bin/kctool.lua"
FB_DEV="/dev/fb0"
FONT_BOLD="$APP_DIR/fonts/OpenSans-Bold.ttf"
FONT_REG="$APP_DIR/fonts/OpenSans-Regular.ttf"

CLOCK_24H=1
NIGHT_MODE=0
FULL_REFRESH_EVERY=30
SHOW_BATTERY=1
TIME_PX=210
TIME_PX_LANDSCAPE=270
ORIENTATION=portrait
ARROWS_FLIP=0
HUB_URL=""
AUTO_SYNC=1
SYNC_EVERY_MIN=60
INTERNET_SYNC=1
CLOCK_UTC_OFFSET_HOURS=3
[ -f "$APP_DIR/config.sh" ] && . "$APP_DIR/config.sh"
UTC_OFFSET=$((CLOCK_UTC_OFFSET_HOURS * 3600))
[ -f "$STATE" ] && . "$STATE"

is_running() {
	[ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

case "$1" in
	run)
		;;
	stop)
		is_running && kill "$(cat "$PID_FILE")"
		exit 0
		;;
	*)
		if is_running; then
			kill "$(cat "$PID_FILE")"
			exit 0
		fi
		if [ -f "$LOG" ]; then
			tail -c 30000 "$LOG" >"$LOG.tmp" && mv -f "$LOG.tmp" "$LOG"
		fi
		/bin/sh "$APP_DIR/clock.sh" run </dev/null >>"$LOG" 2>&1 &
		exit 0
		;;
esac

log() {
	echo "$(date '+%Y-%m-%d %H:%M:%S') $*"
}

pad() {
	if [ "$1" -lt 10 ]; then echo "0$1"; else echo "$1"; fi
}

# Календарные преобразования (алгоритм Говарда Хиннанта), чтобы не зависеть от date -d.
days_from_civil() {
	dy=$1
	[ "$2" -le 2 ] && dy=$((dy - 1))
	dera=$((dy / 400))
	dyoe=$((dy - dera * 400))
	if [ "$2" -gt 2 ]; then dmp=$(($2 - 3)); else dmp=$(($2 + 9)); fi
	ddoy=$(((153 * dmp + 2) / 5 + $3 - 1))
	ddoe=$((dyoe * 365 + dyoe / 4 - dyoe / 100 + ddoy))
	echo $((dera * 146097 + ddoe - 719468))
}

epoch_to_civil() {
	edays=$(($1 / 86400))
	erem=$(($1 % 86400))
	Ch=$((erem / 3600))
	Cm=$((erem % 3600 / 60))
	Cs=$((erem % 60))
	ez=$((edays + 719468))
	eera=$((ez / 146097))
	edoe=$((ez - eera * 146097))
	eyoe=$(((edoe - edoe / 1460 + edoe / 36524 - edoe / 146096) / 365))
	CY=$((eyoe + eera * 400))
	edoy=$((edoe - (365 * eyoe + eyoe / 4 - eyoe / 100)))
	emp=$(((5 * edoy + 2) / 153))
	CD=$((edoy - (153 * emp + 2) / 5 + 1))
	if [ "$emp" -lt 10 ]; then CM=$((emp + 3)); else CM=$((emp - 9)); fi
	[ "$CM" -le 2 ] && CY=$((CY + 1))
}

days_in_month() {
	case "$1" in
		2)
			if [ $(($2 % 4)) -eq 0 ] && { [ $(($2 % 100)) -ne 0 ] || [ $(($2 % 400)) -eq 0 ]; }; then
				echo 29
			else
				echo 28
			fi
			;;
		4|6|9|11) echo 30 ;;
		*) echo 31 ;;
	esac
}

weekday_name() {
	case "$1" in
		1) echo "Понедельник" ;;
		2) echo "Вторник" ;;
		3) echo "Среда" ;;
		4) echo "Четверг" ;;
		5) echo "Пятница" ;;
		6) echo "Суббота" ;;
		*) echo "Воскресенье" ;;
	esac
}

month_name() {
	case "$1" in
		1) echo "января" ;;
		2) echo "февраля" ;;
		3) echo "марта" ;;
		4) echo "апреля" ;;
		5) echo "мая" ;;
		6) echo "июня" ;;
		7) echo "июля" ;;
		8) echo "августа" ;;
		9) echo "сентября" ;;
		10) echo "октября" ;;
		11) echo "ноября" ;;
		*) echo "декабря" ;;
	esac
}

month_number() {
	case "$1" in
		Jan) echo 1 ;; Feb) echo 2 ;; Mar) echo 3 ;; Apr) echo 4 ;;
		May) echo 5 ;; Jun) echo 6 ;; Jul) echo 7 ;; Aug) echo 8 ;;
		Sep) echo 9 ;; Oct) echo 10 ;; Nov) echo 11 ;; Dec) echo 12 ;;
	esac
}

save_state() {
	{
		echo "ORIENTATION=$ORIENTATION"
		echo "UTC_OFFSET=$UTC_OFFSET"
	} >"$STATE.tmp" && mv -f "$STATE.tmp" "$STATE"
}

have_lua() {
	[ -n "$LUAJIT" ]
}

# Запускает команду и убивает её, если она не уложилась в N секунд.
run_timeout() {
	rt_limit=$1
	shift
	"$@" &
	rt_pid=$!
	# Вывод сторожа закрыт, иначе $(...) ждал бы, пока доспит его sleep.
	(sleep "$rt_limit"; kill "$rt_pid" 2>/dev/null) >/dev/null 2>&1 </dev/null &
	rt_watch=$!
	wait "$rt_pid"
	rt_rc=$?
	kill "$rt_watch" 2>/dev/null
	return $rt_rc
}

# ---------- батарея ----------

battery_level() {
	bl="$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null | tr -cd '0-9')"
	[ -z "$bl" ] && bl="$(gasgauge-info -s 2>/dev/null | tr -cd '0-9')"
	echo "$bl"
}

charging() {
	if [ "$(lipc-get-prop com.lab126.powerd isCharging 2>/dev/null)" = "1" ]; then echo 1; else echo 0; fi
}

# ---------- экран ----------

if [ "$NIGHT_MODE" = "1" ]; then
	FB_FLAGS="-q -b -h"
	FB_FLAGS_SEL="-q -b"
else
	FB_FLAGS="-q -b"
	FB_FLAGS_SEL="-q -b -h"
fi

text() {
	# text <px> <top> <шрифт> <строка>
	"$FBINK" $FB_FLAGS -m -t "regular=$3,px=$1,top=$2" "$4" >/dev/null 2>&1
}

refresh_all() {
	if [ "$1" = "1" ]; then
		"$FBINK" -q -f -s >/dev/null 2>&1
	else
		"$FBINK" -q -s >/dev/null 2>&1
	fi
}

read_fb_info() {
	if have_lua; then
		set -- $("$LUAJIT" "$KCTOOL" fbinfo 2>>"$LOG")
		XRES=$1 YRES=$2 BPP=$3 LL=$4
	fi
	if [ -z "$LL" ]; then
		eval "$("$FBINK" -e 2>/dev/null)"
		XRES=$screenWidth YRES=$screenHeight LL=$lineLength
	fi
	if [ "$XRES" -gt "$YRES" ]; then
		L_WD_PX=44 L_WD_TOP=40
		L_TIME_PX=$TIME_PX_LANDSCAPE L_TIME_TOP=95
		L_LINE_TOP=420
		L_DATE_PX=46 L_DATE_TOP=445
	else
		L_WD_PX=46 L_WD_TOP=160
		L_TIME_PX=$TIME_PX L_TIME_TOP=235
		L_LINE_TOP=500
		L_DATE_PX=46 L_DATE_TOP=530
	fi
	L_LINE_LEFT=$(((XRES - 300) / 2))
	L_BAT_TOP=$((YRES - 55))
}

peek_fb() {
	if have_lua; then
		"$LUAJIT" "$KCTOOL" peek 2 2>>"$LOG"
		return
	fi
	set -- $(dd if="$FB_DEV" bs=2 count=1 2>/dev/null | od -b)
	echo "$((0$2)) $((0$3))"
}

# Узнаём, как в видеопамяти кодируются белый и чёрный: от этого зависит кадр с ПК.
calibrate() {
	"$FBINK" -q -b -k >/dev/null 2>&1
	"$FBINK" -q -b -B BLACK -k top=0,left=0,width=1,height=1 >/dev/null 2>&1
	set -- $(peek_fb)
	cb0=${1:-0}
	cb1=${2:-0}
	NIB=hi
	if [ "$BPP" = "4" ]; then
		WHITE=$((cb1 & 15))
		if [ $((cb0 & 15)) -ne "$WHITE" ] && [ $((cb0 >> 4)) -eq "$WHITE" ]; then
			NIB=lo
		fi
	else
		WHITE=$cb1
	fi
	log "Экран ${XRES}x${YRES}, ${BPP} bpp, строка $LL байт, белый=$WHITE, порядок=$NIB, поворот на ПК=$SOFT_ROT"
}

apply_orientation() {
	SOFT_ROT=0
	case "$ORIENTATION" in
		landscape) want=2 ;;
		landscape_flip) want=3 ;;
		*) want=0 ;;
	esac
	hw_ok=0
	if have_lua; then
		if [ "$("$LUAJIT" "$KCTOOL" orient get 2>>"$LOG")" = "$want" ]; then
			hw_ok=1
		elif [ "$("$LUAJIT" "$KCTOOL" orient set "$want" 2>>"$LOG")" = "ok" ]; then
			hw_ok=1
		fi
	fi
	if [ "$hw_ok" = "0" ] && [ "$want" != "0" ]; then
		if [ "$want" = "2" ]; then SOFT_ROT=90; else SOFT_ROT=270; fi
		log "Поворот драйвером не сработал: горизонтальный режим только с ПК"
	fi
	XRES="" YRES="" BPP="" LL=""
	read_fb_info
	calibrate
	HUB_SEQ=0
	OFF_HASH=""
	NEXT_PACK=0
}

draw_local() {
	set -- $(date '+%H %M %u %d %m %Y')
	hour="$1"
	if [ "$CLOCK_24H" != "1" ]; then
		hour="${hour#0}"
		[ "$hour" -eq 0 ] && hour=12
		[ "$hour" -gt 12 ] && hour=$((hour - 12))
	fi

	"$FBINK" $FB_FLAGS -k >/dev/null 2>&1
	text "$L_WD_PX" "$L_WD_TOP" "$FONT_REG" "$(weekday_name "$3")"
	text "$L_TIME_PX" "$L_TIME_TOP" "$FONT_BOLD" "$hour:$2"
	"$FBINK" $FB_FLAGS -B BLACK -k top=$L_LINE_TOP,left=$L_LINE_LEFT,width=300,height=3 >/dev/null 2>&1
	text "$L_DATE_PX" "$L_DATE_TOP" "$FONT_REG" "${4#0} $(month_name "${5#0}") $6"

	status=""
	if [ "$SHOW_BATTERY" = "1" ]; then
		bl="$(battery_level)"
		if [ -n "$bl" ]; then
			status="Заряд $bl%"
			[ "$(charging)" = "1" ] && status="$status, заряжается"
		fi
	fi
	if [ -n "$HUB_URL" ]; then
		[ -n "$status" ] && status="$status · "
		if [ "$(wifi_state)" = "CONNECTED" ]; then
			status="${status}ПК не отвечает"
			wn=$(wifi_name)
			[ -n "$wn" ] && status="$status, Wi-Fi «$wn»"
		else
			status="${status}нет Wi-Fi"
		fi
	fi
	[ -n "$status" ] && text 22 "$L_BAT_TOP" "$FONT_REG" "$status"

	refresh_all "$1"
	HUB_SEQ=0
	OFF_HASH=""
}

toast() {
	"$FBINK" $FB_FLAGS_SEL -m -t "regular=$FONT_REG,px=26,top=$((YRES - 60)),padding=HORIZONTAL" "$1" >/dev/null 2>&1
	"$FBINK" -q -s top=$((YRES - 70)),left=0,width=$XRES,height=70 >/dev/null 2>&1
	HUB_SEQ=0
}

# ---------- связь с ПК ----------

fetch() {
	rm -f "$2"
	if have_lua; then
		NET_ERR="$(run_timeout $(($3 + 5)) "$LUAJIT" "$KCTOOL" get "$HUB_URL$1" "$2" "$3" 2>&1)"
	else
		NET_ERR="$(run_timeout "$3" wget -q -O "$2" "$HUB_URL$1" 2>&1)"
	fi
	[ -s "$2" ] && return 0
	NET_ERR="${NET_ERR#kctool: }"
	[ -z "$NET_ERR" ] && NET_ERR="нет ответа за $3 с"
	return 1
}

wifi_state() {
	lipc-get-prop com.lab126.wifid cmState 2>/dev/null
}

# На Kindle 3 у wifid нет свойства с именем сети, поэтому пробуем по очереди.
wifi_name() {
	wn=$(lipc-get-prop com.lab126.wifid currentEssid 2>/dev/null)
	[ -z "$wn" ] && wn=$(wpa_cli -i wlan0 status 2>/dev/null | sed -n 's/^ssid=//p')
	[ -z "$wn" ] && wn=$(iwconfig wlan0 2>/dev/null | sed -n 's/.*ESSID:"\(.*\)".*/\1/p')
	echo "$wn"
}

ping_ok() {
	run_timeout 4 ping -c 1 "$1" >/dev/null 2>&1 && echo "да" || echo "нет"
}

# Самопроверка сети при потере связи: по ней видно, чья это проблема.
# ПК отвечает на ping, но порт недоступен — подключения режет ПК (VPN, брандмауэр);
# не отвечает — Kindle не в той сети или связь между ними блокирует роутер.
net_diag() {
	nd_host=${HUB_URL#http://}
	nd_host=${nd_host%%/*}
	nd_host=${nd_host%%:*}
	set -- $(route -n 2>/dev/null | grep '^0\.0\.0\.0')
	nd_gw=$2
	set -- $(grep "^$nd_host " /proc/net/arp 2>/dev/null)
	nd_mac=$4
	nd_ip=$(ifconfig wlan0 2>/dev/null | grep -o 'inet addr:[0-9.]*' | cut -d: -f2)
	log "Проверка сети: Wi-Fi «$(wifi_name)» (подключён: $(wifi_state)), адрес ${nd_ip:-нет}, роутер ${nd_gw:-нет} отвечает: $(ping_ok "$nd_gw"), ПК $nd_host отвечает на ping: $(ping_ok "$nd_host"), MAC ПК: ${nd_mac:-не найден}"
	NET_DIAG_AT=$(date +%s)
}

note_net_error() {
	if [ "$NET_ERR" != "$LAST_NET_ERR" ]; then
		log "Нет связи с ПК: $NET_ERR (Wi-Fi: $(wifi_state))"
		LAST_NET_ERR=$NET_ERR
		net_diag
	elif [ $(($(date +%s) - NET_DIAG_AT)) -ge 900 ]; then
		log "Всё ещё нет связи с ПК: $NET_ERR"
		net_diag
	fi
}

parse_meta() {
	M_SEQ="" M_TOP="" M_ROWS="" M_FLASH=0
	while IFS='=' read -r mk mv; do
		case "$mv" in
			''|*[!0-9]*) continue ;;
		esac
		case "$mk" in
			seq) M_SEQ=$mv ;;
			top) M_TOP=$mv ;;
			rows) M_ROWS=$mv ;;
			flash) M_FLASH=$mv ;;
		esac
	done <"$RUN_DIR/meta"
	[ -n "$M_SEQ" ] && [ -n "$M_TOP" ] && [ -n "$M_ROWS" ] && [ $((M_TOP + M_ROWS)) -le "$YRES" ]
}

hub_update() {
	[ -n "$HUB_URL" ] || return 1
	q="w=$XRES&h=$YRES&bpp=$BPP&ll=$LL&white=$WHITE&nib=$NIB&rot=$SOFT_ROT&have=$HUB_SEQ"
	q="$q&batt=$(battery_level)&chg=$(charging)&h24=$CLOCK_24H&night=$NIGHT_MODE&orient=$ORIENTATION"
	q="$q&essid=$(wifi_name | sed 's/%/%25/g; s/ /%20/g; s/&/%26/g; s/#/%23/g; s/+/%2B/g; s/?/%3F/g')"
	fetch "/api/frame?$q" "$RUN_DIR/meta" 6 || return 1
	parse_meta || return 1
	LAST_ONLINE_AT=$(date +%s)
	pack_sync_due
	[ "$M_ROWS" -eq 0 ] && return 0
	fetch "/api/px/$M_SEQ" "$RUN_DIR/px.gz" 10 || return 1
	gunzip -c "$RUN_DIR/px.gz" >"$RUN_DIR/px.raw" 2>/dev/null || return 1
	[ $(($(wc -c <"$RUN_DIR/px.raw"))) -eq $((M_ROWS * LL)) ] || return 1
	dd if="$RUN_DIR/px.raw" of="$FB_DEV" bs="$LL" seek="$M_TOP" 2>/dev/null || return 1
	if [ "$1" = "1" ] || [ "$M_FLASH" = "1" ]; then
		refresh_all 1
	else
		"$FBINK" -q -s top=$M_TOP,left=0,width=$XRES,height=$M_ROWS >/dev/null 2>&1
	fi
	HUB_SEQ=$M_SEQ
	OFF_HASH=""
	return 0
}

# ---------- запас кадров на время без связи ----------
# ПК заранее рисует экран на сутки вперёд (без часов), Kindle без связи выбирает кадр
# по времени и сам дорисовывает цифры и строку состояния.

pack_sync_due() {
	[ "$SOFT_ROT" = "0" ] && have_lua || return 0
	[ "$(date +%s)" -ge "$NEXT_PACK" ] || return 0
	[ -n "$PACK_PID" ] && kill -0 "$PACK_PID" 2>/dev/null && return 0
	NEXT_PACK=$(($(date +%s) + 900))
	pack_sync </dev/null >>"$LOG" 2>&1 3>&- &
	PACK_PID=$!
}

# Работает в фоне: качает список кадров и недостающие кадры, затем подменяет список целиком.
pack_sync() {
	trap - TERM INT HUP
	q="w=$XRES&h=$YRES&bpp=$BPP&ll=$LL&white=$WHITE&nib=$NIB&rot=$SOFT_ROT&h24=$CLOCK_24H&night=$NIGHT_MODE"
	for ps_try in 1 2 3 4 5 6; do
		fetch "/api/pack?$q" "$RUN_DIR/pack.new" 10 || return
		[ "$(head -n 1 "$RUN_DIR/pack.new")" = "building" ] || break
		sleep 20
	done
	grep -q '^seg ' "$RUN_DIR/pack.new" || return
	mkdir -p "$PACK_DIR"
	for ps_hash in $(sed -n 's/^seg [0-9]* [0-9]* \([0-9a-f]*\) .*/\1/p' "$RUN_DIR/pack.new"); do
		[ -s "$PACK_DIR/$ps_hash.gz" ] && continue
		fetch "/api/packframe/$ps_hash" "$RUN_DIR/frame.part" 15 || return
		mv -f "$RUN_DIR/frame.part" "$PACK_DIR/$ps_hash.gz"
	done
	mv -f "$RUN_DIR/pack.new" "$PACK_DIR/manifest.txt"
	for ps_file in "$PACK_DIR"/*.gz; do
		ps_name=$(basename "$ps_file" .gz)
		grep -q " $ps_name " "$PACK_DIR/manifest.txt" || rm -f "$ps_file"
	done
}

is_num() {
	case "$1" in ''|*[!0-9-]*) return 1 ;; esac
}

draw_offline() {
	[ "$SOFT_ROT" = "0" ] && [ -f "$PACK_DIR/manifest.txt" ] || return 1
	do_now=$(date +%s)
	do_fmt="" do_hash="" do_inv=0
	while read -r kind a b c d e f; do
		case "$kind" in
			fmt) do_fmt="$a $b $c $d $e $f" ;;
			clock) PC_L=$a PC_R=$b PC_CT=$c PC_CH=$d PC_TOP=$e PC_PX=$f ;;
			status) PS_T=$a PS_H=$b ;;
			seg) is_num "$a" && is_num "$b" && [ "$a" -le "$do_now" ] && [ "$do_now" -lt "$b" ] && do_hash=$c do_inv=$d ;;
		esac
	done <"$PACK_DIR/manifest.txt"
	[ "$do_fmt" = "$XRES $YRES $BPP $LL $WHITE $NIB" ] && [ -n "$do_hash" ] && [ -f "$PACK_DIR/$do_hash.gz" ] || return 1
	for v in "$PC_L" "$PC_R" "$PC_CT" "$PC_CH" "$PC_TOP" "$PC_PX" "$PS_T" "$PS_H"; do
		is_num "$v" || return 1
	done

	do_full=$1
	if [ "$do_hash" != "$OFF_HASH" ]; then
		gunzip -c "$PACK_DIR/$do_hash.gz" >"$RUN_DIR/off.raw" 2>/dev/null || return 1
		[ $(($(wc -c <"$RUN_DIR/off.raw"))) -eq $((YRES * LL)) ] || return 1
		dd if="$RUN_DIR/off.raw" of="$FB_DEV" bs="$LL" 2>/dev/null || return 1
		OFF_HASH=$do_hash
		do_full=1
	fi
	if [ "$do_inv" = "1" ]; then do_flags="-q -b -h"; else do_flags="-q -b"; fi

	set -- $(date '+%H %M')
	do_hour="$1"
	if [ "$CLOCK_24H" != "1" ]; then
		do_hour="${do_hour#0}"
		[ "$do_hour" -eq 0 ] && do_hour=12
		[ "$do_hour" -gt 12 ] && do_hour=$((do_hour - 12))
	fi
	"$FBINK" $do_flags -k top=$PC_CT,left=$PC_L,width=$((PC_R - PC_L)),height=$PC_CH >/dev/null 2>&1
	# Без фона (-O): иначе fbink закрасит всю высоту строки шрифта и срежет дату под цифрами.
	"$FBINK" $do_flags -O -m -t "regular=$FONT_BOLD,px=$PC_PX,top=$PC_TOP,left=$PC_L,right=$((XRES - PC_R))" "$do_hour:$2" >/dev/null 2>&1

	do_status=""
	bl="$(battery_level)"
	[ -n "$bl" ] && do_status="Заряд $bl% · "
	if [ "$LAST_ONLINE_AT" -gt 0 ]; then
		epoch_to_civil $((LAST_ONLINE_AT + UTC_OFFSET))
		do_status="${do_status}нет связи с ПК с $(pad $Ch):$(pad $Cm)"
	else
		do_status="${do_status}нет связи с ПК"
	fi
	"$FBINK" $do_flags -k top=$PS_T,left=0,width=$XRES,height=$PS_H >/dev/null 2>&1
	"$FBINK" $do_flags -m -t "regular=$FONT_REG,px=22,top=$((PS_T + 3))" "$do_status" >/dev/null 2>&1

	if [ "$do_full" = "1" ]; then
		refresh_all 1
	else
		"$FBINK" -q -s top=$PC_CT,left=$PC_L,width=$((PC_R - PC_L)),height=$PC_CH >/dev/null 2>&1
		"$FBINK" -q -s top=$PS_T,left=0,width=$XRES,height=$PS_H >/dev/null 2>&1
	fi
	HUB_SEQ=0
	return 0
}

hub_probe() {
	[ -n "$HUB_URL" ] && fetch "/api/ping" "$RUN_DIR/ping" 3
}

update_screen() {
	now=$(date +%s)
	if [ "$HUB_STATE" = "online" ]; then
		hub_update "$1" && return
		# Wi-Fi Kindle иногда пропадает на пару секунд: один повтор, прежде чем уйти в свои часы.
		sleep 2
		hub_update "$1" && return
		note_net_error
		HUB_STATE=offline
		HUB_NEXT_PROBE=$((now + PROBE_EVERY))
	fi
	draw_offline "$1" || draw_local "$1"
	hub_probe_due
}

# Без связи ПК проверяется каждые PROBE_EVERY секунд, а не только раз в минуту.
hub_probe_due() {
	[ -n "$HUB_URL" ] && [ "$HUB_STATE" != "online" ] || return
	[ "$(date +%s)" -ge "$HUB_NEXT_PROBE" ] || return
	hub_connect || HUB_NEXT_PROBE=$(($(date +%s) + PROBE_EVERY))
}

hub_connect() {
	[ -n "$HUB_URL" ] || return 1
	if hub_probe && hub_update 1; then
		NEXT_PACK=0
		pack_sync_due
		HUB_STATE=online
		LAST_NET_ERR=""
		log "ПК на связи"
		return 0
	fi
	note_net_error
	return 1
}

# ---------- время ----------

# Часовой пояс для показа времени: смещение от UTC, которое сообщил ПК.
# Пояс, настроенный в самом Kindle, не используется: системные часы Kindle идут в UTC.
apply_tz() {
	tz_off=$UTC_OFFSET
	tz_sign=-
	if [ "$tz_off" -lt 0 ]; then
		tz_sign=+
		tz_off=$((-tz_off))
	fi
	tz_min=$((tz_off % 3600 / 60))
	TZ="LOC$tz_sign$((tz_off / 3600))"
	[ "$tz_min" -ne 0 ] && TZ="$TZ:$(pad $tz_min)"
	export TZ
}

# Ставит системные часы на указанный момент (секунды UTC).
set_clock_epoch() {
	sc_target=$1
	sc_diff=$((sc_target - $(date +%s)))
	[ "$sc_diff" -ge -1 ] && [ "$sc_diff" -le 1 ] && return 0
	epoch_to_civil "$sc_target"
	sc_utc="$CY-$(pad $CM)-$(pad $CD) $(pad $Ch):$(pad $Cm):$(pad $Cs)"
	sc_utc_old="$(pad $CM)$(pad $CD)$(pad $Ch)$(pad $Cm)$CY.$(pad $Cs)"
	epoch_to_civil $((sc_target + UTC_OFFSET))
	sc_loc="$CY-$(pad $CM)-$(pad $CD) $(pad $Ch):$(pad $Cm):$(pad $Cs)"
	for sc_try in 1 2 3; do
		case "$sc_try" in
			1) date -u -s "$sc_utc" >/dev/null 2>&1 ;;
			2) date -s "$sc_loc" >/dev/null 2>&1 ;;
			3) date -u "$sc_utc_old" >/dev/null 2>&1 || date -u -s "$sc_utc_old" >/dev/null 2>&1 ;;
		esac
		sc_left=$((sc_target - $(date +%s)))
		if [ "$sc_left" -ge -3 ] && [ "$sc_left" -le 3 ]; then
			hwclock -w -u >/dev/null 2>&1 || hwclock -w >/dev/null 2>&1
			log "Часы переведены на $sc_diff с"
			return 0
		fi
	done
	log "Не удалось установить время: нужно $sc_loc, на часах $(date '+%Y-%m-%d %H:%M:%S')"
	return 1
}

# Время по заголовку Date любого веб-сервера (на случай, если ПК выключен).
internet_utc() {
	hdr="$RUN_DIR/headers"
	rm -f "$hdr"
	if have_lua; then
		run_timeout 13 "$LUAJIT" "$KCTOOL" headers http://www.google.com/ 8 >"$hdr" 2>/dev/null
	fi
	iu_line="$(grep -i '^date:' "$hdr" 2>/dev/null | head -n 1)"
	if [ -z "$iu_line" ]; then
		run_timeout 8 sh -c "wget -S -O /dev/null http://www.google.com/ >$hdr 2>&1"
		iu_line="$(grep -i 'date:' "$hdr" 2>/dev/null | head -n 1)"
	fi
	if [ -z "$iu_line" ]; then
		printf 'HEAD / HTTP/1.0\r\nHost: www.google.com\r\n\r\n' >"$RUN_DIR/request"
		run_timeout 8 sh -c "nc www.google.com 80 <$RUN_DIR/request >$hdr 2>/dev/null"
		iu_line="$(grep -i 'date:' "$hdr" 2>/dev/null | head -n 1)"
	fi
	iu_line="$(echo "$iu_line" | tr -d '\r,')"
	set -- $iu_line
	[ $# -ge 6 ] || return 1
	iu_mon="$(month_number "$4")"
	[ -n "$iu_mon" ] || return 1
	iu_day=${3#0}
	iu_year=$5
	iu_oldifs="$IFS"
	IFS=:
	set -- $6
	IFS="$iu_oldifs"
	iu_h=${1#0} iu_m=${2#0} iu_s=${3#0}
	echo $(($(days_from_civil "$iu_year" "$iu_mon" "$iu_day") * 86400 + iu_h * 3600 + iu_m * 60 + iu_s))
}

sync_time() {
	SYNC_RESULT=""
	st_utc=""
	if [ -n "$HUB_URL" ] && fetch "/api/time" "$RUN_DIR/time" 5; then
		st_pc="" st_off=""
		while IFS='=' read -r tk tv; do
			case "$tk" in
				utc) case "$tv" in ''|*[!0-9]*) ;; *) st_pc=$tv ;; esac ;;
				utcoff) case "$tv" in ''|-|*[!0-9-]*) ;; *) st_off=$tv ;; esac ;;
			esac
		done <"$RUN_DIR/time"
		if [ -n "$st_off" ] && [ "$st_off" != "$UTC_OFFSET" ]; then
			log "Часовой пояс ПК: $st_off с от UTC"
			UTC_OFFSET=$st_off
			apply_tz
			save_state
		fi
		if [ -n "$st_pc" ] && set_clock_epoch "$st_pc"; then
			SYNC_RESULT="Время сверено с ПК"
			return 0
		fi
	fi
	if [ "$INTERNET_SYNC" = "1" ]; then
		st_utc="$(internet_utc)"
		if [ -n "$st_utc" ] && set_clock_epoch "$st_utc"; then
			SYNC_RESULT="Время сверено через интернет"
			return 0
		fi
	fi
	SYNC_RESULT="Не удалось сверить время"
	log "$SYNC_RESULT (ПК: ${NET_ERR:-не настроен}; интернет: ${st_utc:-нет ответа})"
	return 1
}

# ---------- ручная настройка времени ----------

# Экран медленный, поэтому нажатия только меняют значения и помечают строки,
# а перерисовка идёт одна на пачку нажатий (событие flush) и только изменённых строк.

settings_label() {
	case "$1" in
		0) echo "День  $(pad $F_D)" ;;
		1) echo "Месяц  $(pad $F_MO)" ;;
		2) echo "Год  $F_Y" ;;
		3) echo "Часы  $(pad $F_H)" ;;
		*) echo "Минуты  $(pad $F_MI)" ;;
	esac
}

settings_row() {
	sr_top=$((row_top + $1 * row_h))
	[ "$2" = "noclear" ] || "$FBINK" $FB_FLAGS -k top=$((sr_top - 6)),left=0,width=$XRES,height=$((row_h - 2)) >/dev/null 2>&1
	if [ "$1" = "$SEL" ]; then
		"$FBINK" $FB_FLAGS_SEL -m -t "regular=$FONT_BOLD,px=$row_px,top=$sr_top,left=60,right=60,padding=HORIZONTAL" "$(settings_label "$1")" >/dev/null 2>&1
	else
		text "$row_px" "$sr_top" "$FONT_REG" "$(settings_label "$1")"
	fi
}

settings_draw_all() {
	row_top=$((YRES * 18 / 100))
	row_h=$((YRES * 11 / 100))
	row_px=$((row_h * 55 / 100))
	"$FBINK" $FB_FLAGS -k >/dev/null 2>&1
	text 40 $((row_top - row_h - 20)) "$FONT_BOLD" "Настройка времени"
	for i in 0 1 2 3 4; do
		settings_row "$i" noclear
	done
	text 22 $((row_top + 5 * row_h + 10)) "$FONT_REG" "$(printf 'Вверх и вниз: поле. Влево и вправо: ±1\nЛистание страниц: ±10\nEnter: сохранить. Back: отмена')"
	refresh_all 1
	DIRTY=""
}

settings_mark() {
	case " $DIRTY " in
		*" $1 "*) ;;
		*) DIRTY="$DIRTY $1" ;;
	esac
}

settings_redraw() {
	[ -n "$DIRTY" ] || return
	sd_min=4 sd_max=0
	for i in $DIRTY; do
		settings_row "$i"
		[ "$i" -lt "$sd_min" ] && sd_min=$i
		[ "$i" -gt "$sd_max" ] && sd_max=$i
	done
	"$FBINK" -q -s top=$((row_top + sd_min * row_h - 6)),left=0,width=$XRES,height=$(((sd_max - sd_min + 1) * row_h)) >/dev/null 2>&1
	DIRTY=""
}

settings_adjust() {
	case "$SEL" in
		0)
			dim=$(days_in_month "$F_MO" "$F_Y")
			F_D=$(((F_D - 1 + $1 % dim + dim) % dim + 1))
			;;
		1) F_MO=$(((F_MO - 1 + $1 % 12 + 12) % 12 + 1)) ;;
		2)
			F_Y=$((F_Y + $1))
			[ "$F_Y" -lt 2020 ] && F_Y=2020
			[ "$F_Y" -gt 2099 ] && F_Y=2099
			;;
		3) F_H=$(((F_H + $1 % 24 + 24) % 24)) ;;
		4) F_MI=$(((F_MI + $1 % 60 + 60) % 60)) ;;
	esac
	dim=$(days_in_month "$F_MO" "$F_Y")
	if [ "$F_D" -gt "$dim" ]; then
		F_D=$dim
		settings_mark 0
	fi
	settings_mark "$SEL"
}

settings_select() {
	settings_mark "$SEL"
	SEL=$(((SEL + $1 + 5) % 5))
	settings_mark "$SEL"
}

# Пока экран перерисовывается целиком, нажатия отбрасываются (их отсекает kctool),
# иначе они копятся и срабатывают потом: второй Back закрывал бы уже сами часы.
busy_on() {
	: >"$BUSY"
}

busy_off() {
	rm -f "$BUSY"
}

# Копия экрана часов на время настройки: при выходе она возвращается сразу,
# а с ПК догружается только разница.
screen_save() {
	SNAP_SEQ=$HUB_SEQ
	SNAP_MIN=$LAST_MIN
	dd if="$FB_DEV" of="$RUN_DIR/screen.raw" bs="$LL" count="$YRES" 2>/dev/null || rm -f "$RUN_DIR/screen.raw"
	HUB_SEQ=0
}

screen_restore() {
	[ -f "$RUN_DIR/screen.raw" ] || return 1
	dd if="$RUN_DIR/screen.raw" of="$FB_DEV" bs="$LL" 2>/dev/null || return 1
	rm -f "$RUN_DIR/screen.raw"
	refresh_all 1
	HUB_SEQ=$SNAP_SEQ
	LAST_MIN=$SNAP_MIN
}

settings_open() {
	busy_on
	screen_save
	MODE=settings
	epoch_to_civil $(($(date +%s) + UTC_OFFSET))
	F_D=$CD F_MO=$CM F_Y=$CY F_H=$Ch F_MI=$Cm
	SEL=0
	settings_draw_all
	busy_off
}

settings_close() {
	busy_on
	MODE=clock
	DIRTY=""
	if ! screen_restore; then
		update_screen 1
		LAST_MIN=$(($(date +%s) / 60))
	fi
	[ -n "$1" ] && toast "$1"
	busy_off
}

settings_key() {
	case "$1" in
		103) settings_select -1 ;;
		108) settings_select 1 ;;
		105) settings_adjust -1 ;;
		106) settings_adjust 1 ;;
		191|104|124) settings_adjust 10 ;;
		193|109) settings_adjust -10 ;;
		28|194)
			if set_clock_epoch $(($(days_from_civil "$F_Y" "$F_MO" "$F_D") * 86400 + F_H * 3600 + F_MI * 60 - UTC_OFFSET)); then
				NEXT_SYNC=$(($(date +%s) + SYNC_EVERY_MIN * 60))
				settings_close
			else
				settings_close "Не удалось установить время"
			fi
			;;
		158|139|102) settings_close ;;
	esac
}

# ---------- обработка событий ----------

rotate_next() {
	case "$ORIENTATION" in
		portrait) ORIENTATION=landscape; ro_name="горизонтальная" ;;
		landscape) ORIENTATION=landscape_flip; ro_name="горизонтальная, перевёрнутая" ;;
		*) ORIENTATION=portrait; ro_name="вертикальная" ;;
	esac
	busy_on
	save_state
	apply_orientation
	update_screen 1
	toast "Ориентация: $ro_name"
	busy_off
}

clock_key() {
	case "$1" in
		102|158) cleanup ;;
		139) settings_open ;;
		31)
			busy_on
			toast "Сверяю время…"
			sync_time
			NEXT_SYNC=$(($(date +%s) + SYNC_EVERY_MIN * 60))
			update_screen 1
			LAST_MIN=$(($(date +%s) / 60))
			toast "$SYNC_RESULT"
			busy_off
			;;
		19) rotate_next ;;
		191|193|104|194)
			busy_on
			update_screen 1
			busy_off
			;;
	esac
}

# Автоповтор при удержании клавиши не используется: на e-ink он копит лишние шаги.
on_key() {
	[ "$2" = "1" ] || return
	ok_code=$(screen_arrow "$1")
	if [ "$MODE" = "settings" ]; then
		settings_key "$ok_code"
	else
		clock_key "$ok_code"
	fi
}

# Стрелки джойстика поворачиваются вместе с экраном: «вверх» — всегда к верху картинки.
# landscape: клавиатура слева от экрана, landscape_flip: справа.
screen_arrow() {
	sa_mode=$ORIENTATION
	if [ "$XRES" -le "$YRES" ]; then
		sa_mode=portrait
	elif [ "$ARROWS_FLIP" = "1" ]; then
		[ "$sa_mode" = "landscape" ] && sa_mode=landscape_flip || sa_mode=landscape
	fi
	case "$sa_mode:$1" in
		landscape:103) echo 106 ;;
		landscape:106) echo 108 ;;
		landscape:108) echo 105 ;;
		landscape:105) echo 103 ;;
		landscape_flip:103) echo 105 ;;
		landscape_flip:105) echo 108 ;;
		landscape_flip:108) echo 106 ;;
		landscape_flip:106) echo 103 ;;
		*) echo "$1" ;;
	esac
}

on_tick() {
	TICKS=$((TICKS + 1))
	if [ "$INPUT_MODE" = "grab" ] && [ "$INPUT_OK" != "1" ] && [ "$TICKS" -ge 4 ]; then
		log "Захват клавиш не запустился, выход по любой клавише"
		kill "$INPUT_PID" 2>/dev/null
		start_key_watchers
	fi
	[ "$MODE" = "settings" ] && return
	now=$(date +%s)
	if [ "$AUTO_SYNC" = "1" ] && [ "$now" -ge "$NEXT_SYNC" ]; then
		if sync_time; then
			NEXT_SYNC=$(($(date +%s) + SYNC_EVERY_MIN * 60))
		else
			NEXT_SYNC=$((now + 300))
		fi
		now=$(date +%s)
	fi
	min=$((now / 60))
	if [ "$min" = "$LAST_MIN" ]; then
		hub_probe_due
		return
	fi
	LAST_MIN=$min
	MINUTES=$((MINUTES + 1))
	if [ "$MINUTES" -ge "$FULL_REFRESH_EVERY" ]; then
		MINUTES=0
		update_screen 1
	else
		update_screen 0
	fi
}

start_key_watchers() {
	INPUT_MODE=watch
	for dev in /dev/input/event*; do
		[ -e "$dev" ] || continue
		(
			sleep 2
			dd if="$dev" of=/dev/null bs=16 count=1 2>/dev/null
			echo "key 158 1"
		) >&3 &
		WATCHERS="$WATCHERS $!"
	done
}

start_input() {
	INPUT_OK=0
	if have_lua; then
		INPUT_MODE=grab
		"$LUAJIT" "$KCTOOL" input "$BUSY" >&3 2>>"$LOG" &
		INPUT_PID=$!
	else
		start_key_watchers
	fi
}

CVM_STOPPED=0
VOLUMD_STOPPED=0
ORIG_ORIENT=0

cleanup() {
	log "Выход"
	trap '' TERM INT HUP
	# ПК должен отличать «часы закрыты» от «пропала связь».
	[ "$HUB_STATE" = "online" ] && fetch "/api/bye" "$RUN_DIR/bye" 2
	[ -n "$INPUT_PID" ] && kill "$INPUT_PID" 2>/dev/null
	[ -n "$TICKER_PID" ] && kill "$TICKER_PID" 2>/dev/null
	[ -n "$PACK_PID" ] && kill "$PACK_PID" 2>/dev/null
	for w in $WATCHERS; do
		kill "$w" 2>/dev/null
	done
	[ -n "$WATCHERS" ] && killall dd 2>/dev/null
	if have_lua && [ "$("$LUAJIT" "$KCTOOL" orient get 2>/dev/null)" != "$ORIG_ORIENT" ]; then
		"$LUAJIT" "$KCTOOL" orient set "$ORIG_ORIENT" >/dev/null 2>&1
	fi
	"$FBINK" -q -k >/dev/null 2>&1
	lipc-set-prop com.lab126.powerd preventScreenSaver 0 2>/dev/null
	[ "$VOLUMD_STOPPED" = "1" ] && killall -CONT volumd 2>/dev/null
	if [ "$CVM_STOPPED" = "1" ]; then
		killall -CONT cvm 2>/dev/null
		# Двойное нажатие Menu заставляет интерфейс Kindle перерисовать экран.
		echo 'send 139' >/proc/keypad
		echo 'send 139' >/proc/keypad
	fi
	rm -rf "$RUN_DIR" "$PID_FILE"
	exit 0
}

# ---------- запуск ----------

apply_tz
log "Запуск"
rm -rf "$RUN_DIR"
mkdir -p "$RUN_DIR"
echo $$ >"$PID_FILE"

if ! cp -f "$APP_DIR/bin/fbink" "$FBINK" || ! chmod 755 "$FBINK"; then
	log "Нет $APP_DIR/bin/fbink"
	eips 1 38 "Kindle Clock: no bin/fbink" >/dev/null 2>&1
	rm -rf "$RUN_DIR" "$PID_FILE"
	exit 1
fi
if ! cp -f "$APP_DIR/bin/luajit" "$LUAJIT" 2>/dev/null || ! chmod 755 "$LUAJIT" || ! "$LUAJIT" -e "os.exit(0)" 2>>"$LOG"; then
	log "LuaJIT не запускается: без поворота экрана и ручной настройки"
	LUAJIT=""
fi

trap cleanup TERM INT HUP

# Даём интерфейсу Kindle дорисовать текущий экран, потом замораживаем его.
sleep 1
lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null
killall -STOP cvm 2>/dev/null && CVM_STOPPED=1
killall -STOP volumd 2>/dev/null && VOLUMD_STOPPED=1
have_lua && ORIG_ORIENT="$("$LUAJIT" "$KCTOOL" orient get 2>/dev/null)"
[ -z "$ORIG_ORIENT" ] && ORIG_ORIENT=0

MODE=clock
HUB_STATE=offline
HUB_NEXT_PROBE=0
PROBE_EVERY=30
NET_DIAG_AT=0
NEXT_PACK=0
PACK_PID=""
OFF_HASH=""
LAST_ONLINE_AT=0
HUB_SEQ=0
NEXT_SYNC=0
MINUTES=0
TICKS=0
WATCHERS=""

apply_orientation

rm -f "$FIFO"
mkfifo "$FIFO"
exec 3<>"$FIFO"
start_input
(
	while :; do
		sleep 1
		echo tick
	done
) >&3 &
TICKER_PID=$!

log "Wi-Fi: $(wifi_state), адрес: $(ifconfig wlan0 2>/dev/null | grep -o 'inet addr:[0-9.]*' | cut -d: -f2)"
if ! hub_connect; then
	HUB_NEXT_PROBE=$(($(date +%s) + PROBE_EVERY))
	draw_local 1
fi
LAST_MIN=$(($(date +%s) / 60))

while read -r ev ea eb <&3; do
	case "$ev" in
		tick) on_tick ;;
		key) on_key "$ea" "$eb" ;;
		flush) [ "$MODE" = "settings" ] && settings_redraw ;;
		ready) INPUT_OK=1 ;;
	esac
done
cleanup

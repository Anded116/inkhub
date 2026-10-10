-- kctool: низкоуровневые операции Kindle Clock через LuaJIT FFI.
--   input [ФАЙЛ]        захватить клавиши (EVIOCGRAB) и печатать "key <код> <значение>";
--                       когда поток нажатий затих, печатать "flush";
--                       пока ФАЙЛ существует (экран занят), нажатия отбрасываются
--   fbinfo              печатать "xres yres bpp line_length"
--   peek N              печатать первые N байт видеопамяти (десятичные)
--   orient get          печатать текущую ориентацию экрана (0..3)
--   orient set N        повернуть экран; успех, если xres и yres поменялись как ожидалось
--   get URL FILE [сек]  скачать URL по HTTP в файл; успех только при ответе 200
--   headers URL [сек]   печатать заголовки ответа

local ffi = require("ffi")
local bit = require("bit")
local C = ffi.C

ffi.cdef[[
struct kc_timeval { long tv_sec; long tv_usec; };
struct kc_input_event { struct kc_timeval time; unsigned short type; unsigned short code; int value; };
struct kc_pollfd { int fd; short events; short revents; };
struct kc_sockaddr_in { unsigned short sin_family; unsigned short sin_port; unsigned int sin_addr; unsigned char sin_zero[8]; };
struct kc_hostent { char *h_name; char **h_aliases; int h_addrtype; int h_length; char **h_addr_list; };
int open(const char *path, int flags, ...);
int close(int fd);
int ioctl(int fd, unsigned long request, ...);
int poll(struct kc_pollfd *fds, unsigned long nfds, int timeout);
int read(int fd, void *buf, unsigned int count);
int getppid(void);
int kill(int pid, int sig);
long time(long *t);
int socket(int domain, int type, int protocol);
int connect(int fd, const struct kc_sockaddr_in *addr, unsigned int len);
int send(int fd, const void *buf, unsigned int len, int flags);
int recv(int fd, void *buf, unsigned int len, int flags);
int fcntl(int fd, int cmd, ...);
int getsockopt(int fd, int level, int name, void *value, unsigned int *len);
unsigned short htons(unsigned short value);
struct kc_hostent *gethostbyname(const char *name);
char *strerror(int errnum);
]]

local O_RDONLY = 0
local O_RDWR = 2
local O_NONBLOCK = 2048
local POLLIN = 1
local POLLOUT = 4
local EV_KEY = 1
local EVIOCGRAB = 0x40044590
local FBIOGET_VSCREENINFO = 0x4600
local FBIOGET_FSCREENINFO = 0x4602
local FBIO_EINK_SET_DISPLAY_ORIENTATION = 0x46F0
local FBIO_EINK_GET_DISPLAY_ORIENTATION = 0x46F1
local KEY_HOME = 102
local FLUSH_MS = 120
local AF_INET = 2
local SOCK_STREAM = 1
local F_GETFL = 3
local F_SETFL = 4
local SOL_SOCKET = 1
local SO_ERROR = 4
local EAGAIN = 11
local EINPROGRESS = 115
local MSG_NOSIGNAL = 0x4000

local function die(msg)
	io.stderr:write("kctool: ", msg, "\n")
	os.exit(1)
end

local function errno_text()
	return ffi.string(C.strerror(ffi.errno()))
end

local function open_fb(flags)
	local fd = C.open("/dev/fb0", flags)
	if fd < 0 then
		die("не открыть /dev/fb0")
	end
	return fd
end

local function geometry()
	local fd = open_fb(O_RDONLY)
	local var = ffi.new("uint32_t[40]")
	local fix = ffi.new("uint8_t[80]")
	if C.ioctl(fd, FBIOGET_VSCREENINFO, var) ~= 0 or C.ioctl(fd, FBIOGET_FSCREENINFO, fix) ~= 0 then
		C.close(fd)
		die("не прочитать параметры экрана")
	end
	C.close(fd)
	local line_length = ffi.cast("uint32_t *", ffi.cast("uint8_t *", fix) + 44)[0]
	return tonumber(var[0]), tonumber(var[1]), tonumber(var[6]), tonumber(line_length)
end

local function orient_get()
	local fd = open_fb(O_RDWR)
	local value = ffi.new("int[1]", -1)
	local rc = C.ioctl(fd, FBIO_EINK_GET_DISPLAY_ORIENTATION, value)
	C.close(fd)
	if value[0] >= 0 and value[0] <= 3 then
		return tonumber(value[0])
	end
	if rc >= 0 and rc <= 3 then
		return tonumber(rc)
	end
	return 0
end

local function orient_request(n)
	local fd = open_fb(O_RDWR)
	local rc = C.ioctl(fd, FBIO_EINK_SET_DISPLAY_ORIENTATION, ffi.cast("int", n))
	C.close(fd)
	return rc
end

local function orient_set(n)
	local before = orient_get()
	local rc = orient_request(n)
	local x, y = geometry()
	local landscape = n == 2 or n == 3
	if rc >= 0 and ((landscape and x > y) or (not landscape and x < y)) then
		print("ok")
		return true
	end
	orient_request(before)
	print("fail")
	return false
end

local function peek(n)
	local fd = open_fb(O_RDONLY)
	local buf = ffi.new("uint8_t[?]", n)
	local got = C.read(fd, buf, n)
	C.close(fd)
	if got ~= n then
		die("не прочитать видеопамять")
	end
	local out = {}
	for i = 0, n - 1 do
		out[#out + 1] = tostring(buf[i])
	end
	print(table.concat(out, " "))
end

-- ---------- ввод ----------

-- Пять нажатий Home за 3 секунды: аварийный выход, если основной скрипт завис.
local function emergency_exit(ppid)
	os.execute("killall -CONT cvm 2>/dev/null")
	C.kill(ppid, 15)
	os.exit(0)
end

local function exists(path)
	local f = path and io.open(path, "rb")
	if f then
		f:close()
		return true
	end
	return false
end

local function input(busy_path)
	local fds = {}
	for i = 0, 9 do
		local fd = C.open("/dev/input/event" .. i, O_RDONLY + O_NONBLOCK)
		if fd >= 0 then
			if C.ioctl(fd, EVIOCGRAB, ffi.cast("int", 1)) ~= 0 then
				io.stderr:write("kctool: не удалось захватить event", i, "\n")
			end
			fds[#fds + 1] = fd
		end
	end
	if #fds == 0 then
		die("нет устройств ввода")
	end

	local pfds = ffi.new("struct kc_pollfd[?]", #fds)
	for i, fd in ipairs(fds) do
		pfds[i - 1].fd = fd
		pfds[i - 1].events = POLLIN
	end
	local ev = ffi.new("struct kc_input_event")
	local size = ffi.sizeof(ev)
	local ppid = C.getppid()
	local home_presses = {}
	local pending = false

	io.stdout:write("ready\n")
	io.stdout:flush()
	while true do
		local ready = C.poll(pfds, #fds, pending and FLUSH_MS or 1000)
		if C.getppid() ~= ppid then
			os.exit(0)
		end
		if ready == 0 and pending then
			io.stdout:write("flush\n")
			io.stdout:flush()
			pending = false
		elseif ready > 0 then
			for i = 0, #fds - 1 do
				if bit.band(pfds[i].revents, POLLIN) ~= 0 then
					while C.read(pfds[i].fd, ev, size) == size do
						if ev.type == EV_KEY and ev.value ~= 0 then
							if not exists(busy_path) then
								io.stdout:write("key ", tostring(ev.code), " ", tostring(ev.value), "\n")
								pending = true
							end
							if ev.code == KEY_HOME and ev.value == 1 then
								local now = tonumber(ev.time.tv_sec)
								table.insert(home_presses, now)
								if #home_presses > 5 then
									table.remove(home_presses, 1)
								end
								if #home_presses == 5 and now - home_presses[1] <= 3 then
									emergency_exit(ppid)
								end
							end
						end
					end
				end
			end
			io.stdout:flush()
		end
	end
end

-- ---------- HTTP ----------

local function resolve(host)
	local addr = ffi.new("uint8_t[4]")
	local a, b, c, d = host:match("^(%d+)%.(%d+)%.(%d+)%.(%d+)$")
	if a then
		addr[0], addr[1], addr[2], addr[3] = tonumber(a), tonumber(b), tonumber(c), tonumber(d)
	else
		local he = C.gethostbyname(host)
		if he == nil or he.h_addr_list[0] == nil then
			return nil
		end
		ffi.copy(addr, he.h_addr_list[0], 4)
	end
	return ffi.cast("unsigned int *", addr)[0]
end

local function wait_fd(fd, events, deadline)
	local pfd = ffi.new("struct kc_pollfd[1]")
	pfd[0].fd = fd
	pfd[0].events = events
	local left = deadline - C.time(nil)
	if left <= 0 then
		return false
	end
	return C.poll(pfd, 1, left * 1000) > 0
end

local function http_get(url, timeout)
	local host, port, path = url:match("^http://([^/:]+):?(%d*)(.*)$")
	if not host then
		return nil, "плохой адрес " .. url
	end
	port = tonumber(port) or 80
	if path == "" then
		path = "/"
	end
	local deadline = C.time(nil) + timeout

	local ip = resolve(host)
	if not ip then
		return nil, "не найден адрес " .. host
	end
	local fd = C.socket(AF_INET, SOCK_STREAM, 0)
	if fd < 0 then
		return nil, "socket: " .. errno_text()
	end
	local function fail(msg)
		C.close(fd)
		return nil, msg
	end
	local flags = C.fcntl(fd, F_GETFL, ffi.cast("int", 0))
	C.fcntl(fd, F_SETFL, ffi.cast("int", bit.bor(flags, O_NONBLOCK)))

	local sa = ffi.new("struct kc_sockaddr_in")
	sa.sin_family = AF_INET
	sa.sin_port = C.htons(port)
	sa.sin_addr = ip
	if C.connect(fd, sa, ffi.sizeof(sa)) ~= 0 then
		if ffi.errno() ~= EINPROGRESS then
			return fail("подключение: " .. errno_text())
		end
		if not wait_fd(fd, POLLOUT, deadline) then
			return fail("подключение: нет ответа за " .. timeout .. " с")
		end
		local err = ffi.new("int[1]")
		local len = ffi.new("unsigned int[1]", 4)
		C.getsockopt(fd, SOL_SOCKET, SO_ERROR, err, len)
		if err[0] ~= 0 then
			return fail("подключение: " .. ffi.string(C.strerror(err[0])))
		end
	end

	local request = "GET " .. path .. " HTTP/1.0\r\nHost: " .. host .. "\r\nConnection: close\r\n\r\n"
	local sent = 0
	while sent < #request do
		if not wait_fd(fd, POLLOUT, deadline) then
			return fail("отправка: нет ответа")
		end
		local n = C.send(fd, ffi.cast("const char *", request) + sent, #request - sent, MSG_NOSIGNAL)
		if n < 0 and ffi.errno() ~= EAGAIN then
			return fail("отправка: " .. errno_text())
		end
		if n > 0 then
			sent = sent + n
		end
	end

	local chunks = {}
	local buf = ffi.new("uint8_t[16384]")
	while true do
		if not wait_fd(fd, POLLIN, deadline) then
			return fail("ответ не пришёл за " .. timeout .. " с")
		end
		local n = C.recv(fd, buf, 16384, 0)
		if n == 0 then
			break
		elseif n > 0 then
			chunks[#chunks + 1] = ffi.string(buf, n)
		elseif ffi.errno() ~= EAGAIN then
			return fail("чтение: " .. errno_text())
		end
	end
	C.close(fd)

	local data = table.concat(chunks)
	local split = data:find("\r\n\r\n", 1, true)
	if not split then
		return nil, "ответ без заголовков"
	end
	local headers = data:sub(1, split - 1)
	local body = data:sub(split + 4)
	local status = tonumber(headers:match("^HTTP/%d%.%d (%d+)"))
	local length = tonumber(headers:lower():match("\ncontent%-length:%s*(%d+)"))
	if length and #body < length then
		return nil, "ответ оборвался"
	end
	return status, headers, body
end

local function get(url, file, timeout)
	local status, headers, body = http_get(url, timeout)
	if not status then
		die(headers)
	end
	if status ~= 200 then
		die("HTTP " .. status)
	end
	local f = io.open(file, "wb")
	if not f then
		die("не записать " .. file)
	end
	f:write(body)
	f:close()
end

local function headers(url, timeout)
	local status, head = http_get(url, timeout)
	if not status then
		die(head)
	end
	print(head)
end

local cmd = arg[1]
if cmd == "input" then
	input(arg[2])
elseif cmd == "fbinfo" then
	print(string.format("%d %d %d %d", geometry()))
elseif cmd == "peek" then
	peek(tonumber(arg[2]) or 2)
elseif cmd == "orient" and arg[2] == "get" then
	print(orient_get())
elseif cmd == "orient" and arg[2] == "set" then
	os.exit(orient_set(tonumber(arg[3]) or 0) and 0 or 1)
elseif cmd == "get" and arg[3] then
	get(arg[2], arg[3], tonumber(arg[4]) or 10)
elseif cmd == "headers" and arg[2] then
	headers(arg[2], tonumber(arg[3]) or 10)
else
	die("неизвестная команда")
end

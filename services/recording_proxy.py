# -*- coding: utf-8 -*-
"""
录制代理：HTTP(S) 代理，所有经过的请求/响应写入 browser_packets。
- HTTP：解析请求、转发、录包后返回响应。
- HTTPS：仅处理 CONNECT 隧道，记录 method=CONNECT、url=host:port；不解密内容。
"""
import socket
import threading
import select
# 延迟导入，避免循环依赖；运行时代理线程内调用
def _get_add_packet():
    from services import browser_packets
    return browser_packets.add_packet


def _parse_request_line(line):
    """解析 'METHOD path HTTP/1.x' 或 CONNECT host:port HTTP/1.x。返回 (method, host, port, path, full_url)。"""
    parts = line.strip().split()
    if len(parts) < 2:
        return None
    method = parts[0].upper()
    path = parts[1]
    if method == "CONNECT":
        # path is "host:port"
        if ":" in path:
            host, _, port_str = path.partition(":")
            try:
                port = int(port_str)
            except ValueError:
                port = 443
        else:
            host, port = path, 443
        return (method, host, port, path, "https://%s/" % path if ":" in path else "https://%s:%s/" % (host, port))
    # GET /path HTTP/1.1 -> need to build full URL from Host header later
    return (method, None, None, path, None)


def _forward_and_record_http(client_sock, request_bytes, add_packet):
    """处理 HTTP 请求：从 request_bytes 解析 Host，转发到目标并录包。"""
    if b"\r\n\r\n" not in request_bytes:
        return
    head, body = request_bytes.split(b"\r\n\r\n", 1)
    lines = head.decode("utf-8", errors="replace").split("\r\n")
    req_line = lines[0] if lines else ""
    parsed = _parse_request_line(req_line)
    if not parsed or parsed[0] == "CONNECT":
        return
    method, _, _, path, _ = parsed
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
    host = headers.get("host", "").strip()
    if not host:
        return
    port = 80
    if ":" in host:
        host, _, port_str = host.partition(":")
        try:
            port = int(port_str)
        except ValueError:
            port = 80
    try:
        target = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target.settimeout(30)
        target.connect((host, port))
    except Exception:
        return
    try:
        full_url = "http://%s%s" % (host, path if path.startswith("/") else "/" + path)
        target.sendall(request_bytes)
        # 读响应：先读状态行和头，再读 body（简化：读到对端关闭或超时）
        response_buf = b""
        target.settimeout(10)
        while True:
            try:
                chunk = target.recv(65536)
                if not chunk:
                    break
                response_buf += chunk
            except (socket.timeout, socket.error):
                break
        target.close()
        # 解析响应
        if b"\r\n\r\n" not in response_buf:
            return
        res_head, res_body = response_buf.split(b"\r\n\r\n", 1)
        res_lines = res_head.decode("utf-8", errors="replace").split("\r\n")
        status_line = res_lines[0] if res_lines else ""
        status_code = 200
        if " " in status_line:
            try:
                status_code = int(status_line.split()[1])
            except (IndexError, ValueError):
                pass
        res_headers = {}
        for line in res_lines[1:]:
            if ":" in line:
                k, _, v = line.partition(":")
                res_headers[k.strip().lower()] = v.strip()
        add_packet(
            method=method,
            url=full_url,
            request_headers=dict(headers),
            request_body=body if body else None,
            response_status=status_code,
            response_headers=res_headers,
            response_body=res_body,
        )
        client_sock.sendall(response_buf)
    except Exception:
        try:
            target.close()
        except Exception:
            pass
    finally:
        try:
            client_sock.close()
        except Exception:
            pass


def _handle_connect(client_sock, host, port, add_packet):
    """处理 CONNECT：建立隧道，记录 CONNECT 包，然后双向转发。"""
    url = "https://%s:%s/" % (host, port)
    try:
        target = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target.settimeout(15)
        target.connect((host, port))
        add_packet(
            method="CONNECT",
            url=url,
            request_headers={},
            request_body=None,
            response_status=200,
            response_headers={"content-length": "0"},
            response_body=b"",
        )
        client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        _tunnel(client_sock, target)
    except Exception:
        try:
            client_sock.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
        except Exception:
            pass
    finally:
        try:
            client_sock.close()
        except Exception:
            pass
        try:
            target.close()
        except Exception:
            pass


def _tunnel(client_sock, target_sock):
    """双向转发 client_sock <-> target_sock，直到任一端关闭。"""
    client_sock.setblocking(False)
    target_sock.setblocking(False)
    while True:
        r, _, _ = select.select([client_sock, target_sock], [], [], 60)
        if not r:
            break
        for s in r:
            try:
                data = s.recv(65536)
                if not data:
                    return
                if s is client_sock:
                    target_sock.sendall(data)
                else:
                    client_sock.sendall(data)
            except (socket.error, OSError):
                return


def _handle_client(client_sock, add_packet):
    """处理单客户端连接：读首行判断 HTTP 或 CONNECT，再分发。"""
    try:
        client_sock.settimeout(15)
        first = b""
        while b"\r\n" not in first and len(first) < 8192:
            chunk = client_sock.recv(1)
            if not chunk:
                client_sock.close()
                return
            first += chunk
        line = first.decode("utf-8", errors="replace").split("\r\n")[0]
        parsed = _parse_request_line(line)
        if not parsed:
            client_sock.close()
            return
        method, host, port, path, full_url = parsed
        if method == "CONNECT":
            _handle_connect(client_sock, host, port, add_packet)
            return
        # HTTP: 需要读剩余请求头+body（简化：读到 \r\n\r\n 后根据 Content-Length 读 body）
        header_block = first
        while b"\r\n\r\n" not in header_block and len(header_block) < 32768:
            chunk = client_sock.recv(4096)
            if not chunk:
                break
            header_block += chunk
        if b"\r\n\r\n" not in header_block:
            client_sock.close()
            return
        req_body = b""
        lines = header_block.decode("utf-8", errors="replace").split("\r\n")
        for ln in lines[1:]:
            if ln.lower().startswith("content-length:"):
                try:
                    cl = int(ln.split(":", 1)[1].strip())
                    if cl > 0 and cl < 2 * 1024 * 1024:
                        while len(req_body) < cl:
                            chunk = client_sock.recv(min(65536, cl - len(req_body)))
                            if not chunk:
                                break
                            req_body += chunk
                except (ValueError, IndexError):
                    pass
                break
        full_request = header_block + req_body
        _forward_and_record_http(client_sock, full_request, add_packet)
    except Exception:
        try:
            client_sock.close()
        except Exception:
            pass


def run_proxy(host="127.0.0.1", port=0):
    """
    在调用线程中运行代理服务器。返回实际监听的 (host, port)。
    """
    add_packet = _get_add_packet()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    actual_port = sock.getsockname()[1]
    sock.listen(32)
    sock.settimeout(1.0)
    return sock, actual_port


def accept_loop(sock, stop_event):
    """在循环中 accept 并为每个连接起线程处理。stop_event.set() 时退出。"""
    add_packet = _get_add_packet()
    while not stop_event.is_set():
        try:
            client, _ = sock.accept()
        except (socket.timeout, OSError):
            continue
        t = threading.Thread(target=_handle_client, args=(client, add_packet), daemon=True)
        t.start()


class RecordingProxyServer:
    """可在后台线程中启动/停止的录制代理。"""

    def __init__(self, host="127.0.0.1", port=0):
        self.host = host
        self.port = port
        self._sock = None
        self._thread = None
        self._stop = threading.Event()
        self._actual_port = None

    def start(self):
        if self._sock is not None:
            return self._actual_port
        self._sock, self._actual_port = run_proxy(self.host, self.port)
        self._stop.clear()
        self._thread = threading.Thread(target=accept_loop, args=(self._sock, self._stop), daemon=True)
        self._thread.start()
        return self._actual_port

    @property
    def proxy_url(self):
        if self._actual_port is None:
            return None
        return "http://%s:%s" % (self.host, self._actual_port)

    def stop(self):
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self._thread = None
        self._actual_port = None

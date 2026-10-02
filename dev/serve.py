#!/usr/bin/env python3
"""
只读本地开发服务：查看 GitWildMatchPattern 编译链路的中间产物。

按模式返回规范化结果、锚定标志（POSIX 与 Windows 两套根路径对照）、
正则片段与编译次数。服务只接受 GET 请求，不修改任何状态（只读）。

用法::

	python dev/serve.py [port]

然后浏览器直接打开 http://127.0.0.1:<port>/ 。
"""

import json
import os
import sys
from http.server import (
	BaseHTTPRequestHandler,
	ThreadingHTTPServer)
from urllib.parse import (
	parse_qs,
	urlparse)

# 让脚本可以直接以 `python dev/serve.py` 运行（无需先安装包）。
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from pathspec.patterns._gitwildmatch import (  # noqa: E402
	compile as gw_compile)
from pathspec.patterns._gitwildmatch.normalize import (  # noqa: E402
	is_anchored,
	normalize_pattern)
from pathspec.patterns._gitwildmatch.regex import (  # noqa: E402
	translate_segments)
from pathspec.patterns.gitignore.base import (  # noqa: E402
	GitIgnorePatternError)

_INDEX_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>gitwildmatch 编译链路查看器</title>
<style>
body { font-family: system-ui, sans-serif; margin: 2em; max-width: 960px; }
label { display: block; margin: 0.5em 0 0.2em; }
input, select { padding: 0.3em; min-width: 24em; }
button { padding: 0.3em 1em; }
pre { background: #f4f4f4; padding: 1em; overflow-x: auto; }
table { border-collapse: collapse; margin-top: 1em; }
td, th { border: 1px solid #ccc; padding: 0.3em 0.8em; }
.ok { color: #0a0; } .bad { color: #c00; }
h2 { margin-top: 2em; }
</style>
</head>
<body>
<h1>gitwildmatch 编译链路查看器</h1>

<h2>模式检查</h2>
<label>模式 <input id="pat" value="/src/main.py"></label>
<label>errors <select id="errors">
<option value="">null (默认)</option>
<option value="literal">literal</option>
<option value="null">null</option>
<option value="raise">raise</option>
</select></label>
<p><button onclick="inspect()">检查</button></p>
<pre id="inspect-out"></pre>

<h2>POSIX / Windows 根路径锚定对照</h2>
<label>POSIX 根路径 <input id="posix" value="/repo/src"></label>
<label>Windows 根路径 <input id="windows" value="C:\\repo\\src"></label>
<p><button onclick="compare()">对照</button></p>
<div id="compare-out"></div>

<script>
async function inspect() {
	const params = new URLSearchParams({pattern: document.getElementById('pat').value});
	const errors = document.getElementById('errors').value;
	if (errors) params.set('errors', errors);
	const resp = await fetch('/api/inspect?' + params);
	const data = await resp.json();
	document.getElementById('inspect-out').textContent = JSON.stringify(data, null, 2);
}
async function compare() {
	const params = new URLSearchParams({
		posix: document.getElementById('posix').value,
		windows: document.getElementById('windows').value,
	});
	const resp = await fetch('/api/compare?' + params);
	const data = await resp.json();
	const cls = data.consistent ? 'ok' : 'bad';
	const mark = data.consistent ? '一致 ✓' : '不一致 ✗';
	document.getElementById('compare-out').innerHTML =
		'<table><tr><th>风味</th><th>根路径</th><th>锚定</th></tr>' +
		'<tr><td>posix</td><td>' + data.posix.root + '</td><td>' + data.posix.anchored + '</td></tr>' +
		'<tr><td>windows</td><td>' + data.windows.root + '</td><td>' + data.windows.anchored + '</td></tr>' +
		'</table><p class="' + cls + '">锚定结果' + mark + '</p>';
}
</script>
</body>
</html>
"""


def _inspect_pattern(pattern, errors):
	"""
	检查单个模式，返回可 JSON 序列化的字典：规范化结果、锚定标志
	（POSIX/Windows 对照）、正则片段与编译次数。
	"""
	result = {
		'pattern': pattern,
		'errors': errors,
	}

	try:
		entry = gw_compile.compile_pattern(pattern, errors=errors or None)
	except (GitIgnorePatternError, TypeError, ValueError) as e:
		result['error'] = f"{type(e).__name__}: {e}"
	else:
		normalized = entry.normalized
		if normalized is None:
			result['normalized'] = None
			result['regex_parts'] = []
		else:
			result['normalized'] = {
				'segments': normalized.segments,
				'include': normalized.include,
				'anchored': normalized.anchored,
				'is_dir_pattern': normalized.is_dir_pattern,
				'regex_override': normalized.regex_override,
			}
			if normalized.regex_override is not None:
				result['regex_parts'] = [normalized.regex_override]
			elif normalized.segments is not None:
				try:
					result['regex_parts'] = translate_segments(
						'raise', normalized.is_dir_pattern, normalized.segments,
					)
				except GitIgnorePatternError:
					result['regex_parts'] = None
			else:
				result['regex_parts'] = None

		regex = entry.regex
		result['regex'] = regex if regex is None or isinstance(regex, str) else repr(regex)
		result['include'] = entry.include
		result['compile_count'] = gw_compile.compile_count(pattern, errors or None)

	# 锚定标志：POSIX 与 Windows 两套根路径写法对照。先归一化再判，
	# 同一逻辑模式两种风味的结果应当一致。
	anchor_posix = is_anchored(pattern, 'posix')
	anchor_windows = is_anchored(pattern, 'windows')
	result['anchor'] = {
		'posix': anchor_posix,
		'windows': anchor_windows,
		'consistent': anchor_posix == anchor_windows,
	}

	return result


def _compare_roots(posix_root, windows_root):
	"""
	对照 POSIX 与 Windows 两套根路径的锚定结果。
	"""
	posix_anchored = is_anchored(posix_root, 'posix')
	windows_anchored = is_anchored(windows_root, 'windows')
	return {
		'posix': {'root': posix_root, 'anchored': posix_anchored},
		'windows': {'root': windows_root, 'anchored': windows_anchored},
		'consistent': posix_anchored == windows_anchored,
	}


class _Handler(BaseHTTPRequestHandler):
	"""
	只读请求处理：仅实现 GET，任何写方法都是 405。
	"""

	server_version = 'GitWildMatchDev/1.0'

	def _send_json(self, payload, status=200):
		body = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
		self.send_response(status)
		self.send_header('Content-Type', 'application/json; charset=utf-8')
		self.send_header('Content-Length', str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def _send_html(self, html):
		body = html.encode('utf-8')
		self.send_response(200)
		self.send_header('Content-Type', 'text/html; charset=utf-8')
		self.send_header('Content-Length', str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def do_GET(self):
		parsed = urlparse(self.path)
		query = parse_qs(parsed.query)

		if parsed.path == '/':
			self._send_html(_INDEX_HTML)

		elif parsed.path == '/api/inspect':
			pattern = query.get('pattern', [''])[0]
			errors = query.get('errors', [''])[0]
			self._send_json(_inspect_pattern(pattern, errors))

		elif parsed.path == '/api/compare':
			posix_root = query.get('posix', [''])[0]
			windows_root = query.get('windows', [''])[0]
			self._send_json(_compare_roots(posix_root, windows_root))

		else:
			self._send_json({'error': 'not found'}, status=404)

	def do_POST(self):
		self._send_json({'error': 'read-only server'}, status=405)

	def do_PUT(self):
		self._send_json({'error': 'read-only server'}, status=405)

	def do_DELETE(self):
		self._send_json({'error': 'read-only server'}, status=405)

	def log_message(self, format, *args):
		sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def main():
	port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
	server = ThreadingHTTPServer(('127.0.0.1', port), _Handler)
	url = f"http://127.0.0.1:{port}/"
	print(f"只读开发服务已启动，浏览器直接打开: {url}")
	print("按 Ctrl+C 停止。")
	try:
		server.serve_forever()
	except KeyboardInterrupt:
		pass
	finally:
		server.server_close()


if __name__ == '__main__':
	main()
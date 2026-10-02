"""
Read-only local inspection server for the gitignore (gitwildmatch) compile
chain. It exposes, per pattern, the normalized result, the anchor flag, the
regular expression parts, and the process-wide regex compile count, plus a
side-by-side comparison of anchor detection for POSIX and Windows root paths.

Run from the repository root:

	python dev/serve.py [--port PORT]

Then open http://127.0.0.1:8000/ in a browser. The server is read-only: it
only answers GET/HEAD; every other method is rejected with 405.
"""

import argparse
import json
import ntpath
import posixpath
import sys
from http.server import (
	BaseHTTPRequestHandler,
	ThreadingHTTPServer)
from pathlib import (
	Path)
from urllib.parse import (
	parse_qs,
	urlparse)

# Make the in-repo `pathspec` package importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pathspec.patterns.gitignore.base import (
	GitIgnorePatternError)
from pathspec.patterns.gitignore.spec import (
	GitIgnoreSpecPattern)
from pathspec.patterns.gitignore._cache import (
	REGEX_CACHE)
from pathspec.patterns.gitignore._fragments import (
	build_regex_parts)
from pathspec.patterns.gitignore._normalize import (
	is_root_anchored,
	normalize_pattern)

# Sample root paths used for the POSIX/Windows anchor comparison. Equivalent
# roots (e.g. "/foo" and "C:/foo") must get the same anchor flag from the
# normalize-first judgment, even where `posixpath.isabs`/`ntpath.isabs`
# disagree.
POSIX_ROOTS = [
	'/',
	'/foo',
	'/foo/bar',
	'foo/bar',
	'./foo',
]
WINDOWS_ROOTS = [
	'C:/',
	'C:/foo',
	'C:/foo/bar',
	'C:\\foo',
	'D:\\foo\\bar',
	'foo\\bar',
	'/foo',
]


def inspect_pattern(pattern: str) -> dict:
	"""
	Inspect *pattern* through the whole compile chain: normalization, anchor
	flag, regex fragment assembly, and (cached) compilation. Returns a
	JSON-serializable dict.
	"""
	result = {
		'pattern': pattern,
		'include': None,
		'anchored': None,
		'is_dir_pattern': None,
		'segments': None,
		'regex_override': None,
		'regex_parts': None,
		'regex': None,
		'error': None,
		'compile_count': REGEX_CACHE.compile_count,
	}

	try:
		norm = normalize_pattern(pattern)
	except ValueError as e:
		result['error'] = f'{type(e).__name__}: {e}'
		return result

	result['include'] = norm.include
	result['anchored'] = norm.anchored
	result['is_dir_pattern'] = norm.is_dir_pattern
	result['segments'] = norm.segments
	result['regex_override'] = norm.regex_override

	if norm.include is None:
		# Null-operation (comment, blank, or "/"): nothing to compile.
		return result

	try:
		if norm.regex_override is not None:
			regex_str = norm.regex_override
		else:
			regex_parts = build_regex_parts('literal', norm.is_dir_pattern, norm.segments)
			result['regex_parts'] = regex_parts
			regex_str = ''.join(regex_parts)

		result['regex'] = regex_str

		# Route through the pattern class so compilation goes through the
		# thread-safe regex cache, then report the process-wide compile count.
		GitIgnoreSpecPattern(pattern)
	except GitIgnorePatternError as e:
		result['error'] = f'{type(e).__name__}: {e}'

	result['compile_count'] = REGEX_CACHE.compile_count
	return result


def anchor_rows() -> list:
	"""
	Compare anchor detection for the POSIX and Windows sample root paths:
	`posixpath.isabs`, `ntpath.isabs`, and the normalize-first judgment used
	by the compile chain (:func:`is_root_anchored`).
	"""
	rows = []
	for family, roots in (('posix', POSIX_ROOTS), ('windows', WINDOWS_ROOTS)):
		for root in roots:
			rows.append({
				'family': family,
				'root': root,
				'posixpath.isabs': posixpath.isabs(root),
				'ntpath.isabs': ntpath.isabs(root),
				'normalized': is_root_anchored(root),
			})
	return rows


_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>gitwildmatch compile chain inspector</title>
<style>
body { font-family: system-ui, sans-serif; margin: 2rem; max-width: 70rem; }
table { border-collapse: collapse; margin-top: 1rem; }
th, td { border: 1px solid #ccc; padding: 0.3rem 0.6rem; text-align: left; }
th { background: #f0f0f0; }
code, pre { background: #f6f6f6; padding: 0.1rem 0.3rem; }
pre { padding: 0.8rem; overflow-x: auto; }
.mismatch { background: #ffe0e0; }
.match { background: #e0ffe0; }
</style>
</head>
<body>
<h1>gitwildmatch compile chain inspector</h1>
<form id="f">
<label>Pattern: <input id="p" name="pattern" value="/foo/bar/**" size="40"></label>
<button type="submit">Inspect</button>
</form>
<h2>Result</h2>
<pre id="out">submit a pattern above</pre>
<h2>Anchor comparison: POSIX vs Windows roots</h2>
<table id="anchors">
<thead><tr><th>family</th><th>root</th><th>posixpath.isabs</th><th>ntpath.isabs</th><th>normalized</th></tr></thead>
<tbody></tbody>
</table>
<script>
async function loadAnchors() {
	const rows = await (await fetch('/api/anchors')).json();
	const tbody = document.querySelector('#anchors tbody');
	for (const row of rows) {
		const tr = document.createElement('tr');
		const agree = row['posixpath.isabs'] === row['ntpath.isabs'];
		for (const key of ['family', 'root', 'posixpath.isabs', 'ntpath.isabs', 'normalized']) {
			const td = document.createElement('td');
			td.textContent = String(row[key]);
			if (key.endsWith('isabs')) td.className = agree ? 'match' : 'mismatch';
			tr.appendChild(td);
		}
		tbody.appendChild(tr);
	}
}
document.getElementById('f').addEventListener('submit', async (e) => {
	e.preventDefault();
	const pattern = document.getElementById('p').value;
	const res = await fetch('/api/inspect?pattern=' + encodeURIComponent(pattern));
	document.getElementById('out').textContent = JSON.stringify(await res.json(), null, 2);
});
loadAnchors();
</script>
</body>
</html>
"""


class InspectHandler(BaseHTTPRequestHandler):
	"""
	Read-only request handler. Only GET and HEAD are served; everything else
	is rejected with 405.
	"""

	server_version = 'GitWildMatchInspect/1.0'

	def _send_json(self, payload) -> None:
		body = json.dumps(payload, indent=2).encode('utf-8')
		self.send_response(200)
		self.send_header('Content-Type', 'application/json; charset=utf-8')
		self.send_header('Content-Length', str(len(body)))
		self.end_headers()
		if self.command != 'HEAD':
			self.wfile.write(body)

	def _send_html(self, html: str) -> None:
		body = html.encode('utf-8')
		self.send_response(200)
		self.send_header('Content-Type', 'text/html; charset=utf-8')
		self.send_header('Content-Length', str(len(body)))
		self.end_headers()
		if self.command != 'HEAD':
			self.wfile.write(body)

	def _send_405(self) -> None:
		self.send_response(405)
		self.send_header('Allow', 'GET, HEAD')
		self.send_header('Content-Length', '0')
		self.end_headers()

	def do_GET(self) -> None:
		url = urlparse(self.path)
		if url.path == '/':
			self._send_html(_INDEX_HTML)
		elif url.path == '/api/inspect':
			query = parse_qs(url.query)
			pattern = query.get('pattern', [''])[0]
			self._send_json(inspect_pattern(pattern))
		elif url.path == '/api/anchors':
			self._send_json(anchor_rows())
		else:
			self.send_error(404, 'Not Found')

	def do_HEAD(self) -> None:
		self.do_GET()

	# Read-only: reject every mutating method.
	do_POST = _send_405
	do_PUT = _send_405
	do_PATCH = _send_405
	do_DELETE = _send_405

	def log_message(self, format: str, *args) -> None:
		sys.stderr.write('%s - %s\n' % (self.address_string(), format % args))


def main() -> None:
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument('--port', type=int, default=8000)
	parser.add_argument('--host', default='127.0.0.1')
	args = parser.parse_args()

	server = ThreadingHTTPServer((args.host, args.port), InspectHandler)
	url = f'http://{args.host}:{args.port}/'
	print(f'Serving read-only inspection at {url} (Ctrl+C to stop)')
	try:
		server.serve_forever()
	except KeyboardInterrupt:
		pass
	finally:
		server.server_close()


if __name__ == '__main__':
	main()
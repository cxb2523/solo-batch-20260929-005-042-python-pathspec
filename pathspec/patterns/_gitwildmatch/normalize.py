"""
模式规范化组件：GitWildMatch/gitignore 编译链路的第一阶段。

.. warning:: 本模块不是公共 API，其内容与结构随时可能变化。

本组件把原始模式串规范化为 :class:`NormalizedPattern`，处理：

-	正负前缀：``!`` 前缀取反（include=False），``#`` 前缀是注释（空操作）。
-	尾部斜杠：以 ``/`` 结尾的是目录模式，末段改写为 ``**`` 以匹配所有后代。
-	锚定与相对根：以 ``/`` 开头或中间含 ``/`` 的模式锚定到根目录；单段
	模式前补 ``**`` 段，使其可匹配任意深度的后代路径。
-	``**`` 片段：折叠相邻的 ``**/**``，并对 ``**``、``**/``、``*``、
	``*/`` 等整体匹配模式直接给出正则覆盖（regex override）。

定法：锚定判定「先归一化再判」，不按 posixpath/ntpath 分流。

	判定一个模式是否锚定到根目录时，本组件先把路径分隔符归一化
	（Windows 风味先去盘符、把 ``\\`` 折成 ``/``），再用同一套规则判定，
	而不是按 posixpath/ntpath 各判各的。这决定 Windows 下行为是否一致：
	若按 posixpath/ntpath 分流，同一个逻辑模式（如 "src/main.py" 与
	"src\\main.py"）在 POSIX 与 Windows 下会得到不同的锚定结果——
	Windows 上反斜杠被当作分隔符，而 POSIX 上它只是普通字符，行为随平台
	漂移。先归一化再判的代价是 POSIX 下文件名里合法的反斜杠在「锚定探查」
	语义中会被视为分隔符，但换来的是跨平台一致：同一逻辑模式在任何平台
	上锚定标志相同，这正是 dev/serve.py 能做 POSIX/Windows 锚定对照的
	前提。注意这只影响 dev 工具暴露的锚定探查；gitignore 编译链路本身
	遵循 git 语义，只认 ``/`` 作分隔符。
"""

from dataclasses import (
	dataclass)
from typing import (
	Optional)  # Replaced by `X | None` in 3.10.

from ..gitignore.base import (
	GitIgnorePatternError,
	_strip_trailing_ws)
from .regex import (
	_DIR_MARK_CG,
	_MATCH_ALL)


@dataclass()
class NormalizedPattern(object):
	"""
	The :class:`NormalizedPattern` data class is the result of normalizing a
	gitignore pattern: the include flag, the anchor flag, the directory-pattern
	flag, and either the normalized segments or a regular expression override.
	"""

	# Keep the class dict-less.
	__slots__ = (
		'anchored',
		'include',
		'is_dir_pattern',
		'original',
		'regex_override',
		'segments',
	)

	original: str
	"""
	*original* (:class:`str`) is the pattern string as passed in, before any
	normalization.
	"""

	include: bool
	"""
	*include* (:class:`bool`) is whether matched files should be included
	(:data:`True`) or excluded (:data:`False`). A pattern starting with an
	exclamation mark ('!') negates the pattern.
	"""

	anchored: bool
	"""
	*anchored* (:class:`bool`) is whether the pattern is anchored to the root
	directory (leading or middle slash), as opposed to matching at any depth
	(a ``**`` segment was prepended).
	"""

	is_dir_pattern: bool
	"""
	*is_dir_pattern* (:class:`bool`) is whether the pattern is a directory
	pattern (i.e., ends with a slash '/').
	"""

	segments: Optional[list]
	"""
	*segments* (:class:`list` of :class:`str`; or :data:`None`) is the
	normalized pattern segments, or :data:`None` when *regex_override* is set.
	"""

	regex_override: Optional[str]
	"""
	*regex_override* (:class:`str` or :data:`None`) is the regular expression
	override for patterns that normalize to a match-all form, or :data:`None`
	when *segments* should be used.
	"""


def normalize_segments(
	is_dir_pattern: bool,
	pattern_segs: list,
) -> tuple:
	"""
	Normalize the pattern segments to make processing easier.

	*is_dir_pattern* (:class:`bool`) is whether the pattern is a directory
	pattern (i.e., ends with a slash '/').

	*pattern_segs* (:class:`list` of :class:`str`) contains the pattern
	segments. This may be modified in place.

	Raises :exc:`ValueError` if the pattern normalizes to nothing.

	Returns a :class:`tuple` containing:

	-	The normalized segments (:class:`list` of :class:`str`; or :data:`None`).

	-	The regular expression override (:class:`str` or :data:`None`).

	-	The anchor flag (:class:`bool`).
	"""
	anchored = True

	if not pattern_segs[0]:
		# A pattern beginning with a slash ('/') should match relative to the root
		# directory. Remove the empty first segment to make the pattern relative
		# to root.
		del pattern_segs[0]

	elif len(pattern_segs) == 1 or (len(pattern_segs) == 2 and not pattern_segs[1]):
		# A single segment pattern with or without a trailing slash ('/') will
		# match any descendant path. This is equivalent to "**/{pattern}". Prepend
		# a double-asterisk segment to make the pattern relative to root.
		if pattern_segs[0] != '**':
			pattern_segs.insert(0, '**')
			anchored = False

	else:
		# A pattern without a beginning slash ('/') but contains at least one
		# prepended directory (e.g., "dir/{pattern}") should match relative to
		# the root directory. No segment modification is needed.
		pass

	if not pattern_segs:
		# After normalization, we end up with no pattern at all. This must be
		# because the pattern is invalid.
		raise ValueError("Pattern normalized to nothing.")

	if not pattern_segs[-1]:
		# A pattern ending with a slash ('/') will match all descendant paths if
		# it is a directory but not if it is a regular file. This is equivalent to
		# "{pattern}/**". Set the empty last segment to a double-asterisk to
		# include all descendants.
		pattern_segs[-1] = '**'

	# EDGE CASE: Collapse duplicate double-asterisk sequences (i.e., '**/**').
	# Iterate over the segments in reverse order and remove the duplicate double
	# asterisks as we go.
	for i in range(len(pattern_segs) - 1, 0, -1):
		prev = pattern_segs[i-1]
		seg = pattern_segs[i]
		if prev == '**' and seg == '**':
			del pattern_segs[i]

	seg_count = len(pattern_segs)
	if seg_count == 1 and pattern_segs[0] == '**':
		if is_dir_pattern:
			# The pattern "**/" will be normalized to "**", but it should match
			# everything except for files in the root. Special case this pattern.
			return (None, _DIR_MARK_CG, anchored)
		else:
			# The pattern "**" will match every path. Special case this pattern.
			return (None, _MATCH_ALL, anchored)

	elif (
		seg_count == 2
		and pattern_segs[0] == '**'
		and pattern_segs[1] == '*'
	):
		# The pattern "*" will be normalized to "**/*" and will match every
		# path. Special case this pattern for efficiency.
		return (None, _MATCH_ALL, anchored)

	elif (
		seg_count == 3
		and pattern_segs[0] == '**'
		and pattern_segs[1] == '*'
		and pattern_segs[2] == '**'
	):
		# The pattern "*/" will be normalized to "**/*/**" which will match every
		# file not in the root directory. Special case this pattern for
		# efficiency.
		if is_dir_pattern:
			return (None, _DIR_MARK_CG, anchored)
		else:
			return (None, '/', anchored)

	# No regular expression override, return modified pattern segments.
	return (pattern_segs, None, anchored)


def normalize_pattern(pattern_str: str) -> Optional[NormalizedPattern]:
	"""
	Normalize the pattern string.

	Handles the include/negate prefix ('!'), comment patterns ('#'), blank
	patterns, the directory-pattern trailing slash, anchoring relative to the
	root directory, and double-asterisk ('**') segments.

	*pattern_str* (:class:`str`) is the pattern to normalize. Trailing
	whitespace is stripped first (unless escaped), matching git.

	Raises :exc:`.GitIgnorePatternError` if the pattern normalizes to nothing.

	Returns the :class:`NormalizedPattern`, or :data:`None` when the pattern
	is a null-operation (comment, blank, or a single '/').
	"""
	original_pattern = pattern_str

	# Strip trailing whitespace.
	pattern_str = _strip_trailing_ws(pattern_str)

	if pattern_str.startswith('#'):
		# A pattern starting with a hash ('#') serves as a comment (neither
		# includes nor excludes files). Escape the hash with a backslash to match
		# a literal hash (i.e., '\#').
		return None

	elif pattern_str == '/':
		# EDGE CASE: According to `git check-ignore` (v2.4.1), a single '/' does
		# not match any file.
		return None

	if pattern_str.startswith('!'):
		# A pattern starting with an exclamation mark ('!') negates the pattern
		# (exclude instead of include). Escape the exclamation mark with a
		# backslash to match a literal exclamation mark (i.e., '\!').
		include = False
		# Remove leading exclamation mark.
		pattern_str = pattern_str[1:]
	else:
		include = True

	if not pattern_str:
		# A blank pattern is a null-operation (neither includes nor excludes
		# files).
		return None

	# Split pattern into segments.
	orig_segs = pattern_str.split('/')

	# Check whether the pattern is specifically a directory pattern before
	# normalization.
	is_dir_pattern = not orig_segs[-1]

	# Normalize pattern to make processing easier.
	try:
		pattern_segs, override_regex, anchored = normalize_segments(
			is_dir_pattern, orig_segs,
		)
	except ValueError as e:
		raise GitIgnorePatternError((
			f"Invalid git pattern: {original_pattern!r}"
		)) from e  # GitIgnorePatternError

	return NormalizedPattern(
		original=original_pattern,
		include=include,
		anchored=anchored,
		is_dir_pattern=is_dir_pattern,
		segments=pattern_segs,
		regex_override=override_regex,
	)


def is_anchored(pattern: str, flavor: str = 'posix') -> bool:
	"""
	Check whether the pattern is anchored to the root directory.

	*pattern* (:class:`str`) is the pattern or root path to check.

	*flavor* (:class:`str`) is the path separator flavor: ``'posix'`` or
	``'windows'``. It only selects how separators are normalized; the anchor
	rule itself is identical for both flavors (see the module docstring for
	the rationale of normalizing first instead of dispatching on
	posixpath/ntpath).

	Returns whether the pattern is anchored (:class:`bool`). A pattern is
	anchored when it contains a leading or middle separator (gitignore's
	rule for being relative to the root directory).
	"""
	if flavor == 'windows':
		pattern = _normalize_windows_separators(pattern)
	elif flavor != 'posix':
		raise ValueError(f"{flavor=!r} is not a valid flavor.")

	# A trailing separator only marks a directory pattern and does not anchor
	# it, so judge the body without it.
	body = pattern[:-1] if pattern.endswith('/') else pattern
	return '/' in body


def _normalize_windows_separators(path: str) -> str:
	"""
	Normalize Windows-style separators in *path* (:class:`str`): strip a
	leading drive letter (e.g., "C:") and fold backslashes into slashes.

	Returns the normalized path (:class:`str`).
	"""
	if len(path) >= 2 and path[1] == ':' and path[0].isalpha():
		# Strip the drive letter (e.g., "C:\\foo" -> "\\foo").
		path = path[2:]

	return path.replace('\\', '/')
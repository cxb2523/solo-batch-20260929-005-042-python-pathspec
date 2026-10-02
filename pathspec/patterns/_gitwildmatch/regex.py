"""
正则片段拼装组件：GitWildMatch/gitignore 编译链路的第二阶段。

.. warning:: 本模块不是公共 API，其内容与结构随时可能变化。

本组件把 :mod:`~pathspec.patterns._gitwildmatch.normalize` 规范化出的
模式片段拼装成正则表达式片段序列：

-	:func:`translate_segment_glob` 把单个路径段 glob 翻译成正则片段
	（从 ``pathspec.patterns.gitignore.base`` 迁入，basic 与 spec 共用
	同一份实现，原处保留转发）。
-	:func:`translate_segments` 把整段序列拼装成正则片段列表，处理
	``**`` 的首/中/尾位置、目录标记与根锚定。
"""

import re
from typing import (
	Literal)

from ..gitignore.base import (
	_PosixClassError,
	_RangeNotationError,
	_TrailingBackslashError)

_DIR_MARK = 'ps_d'
"""
The regex group name for the directory marker. This is only used by
:class:`GitIgnoreSpec`.
"""

_DIR_MARK_CG = f'(?P<{_DIR_MARK}>/)'
"""
This regular expression matches the directory marker.
"""

_DIR_MARK_OPT = f'(?:{_DIR_MARK_CG}|$)'
"""
This regular expression matches the optional directory marker and sub-path.
"""

_MATCH_ALL = f'^(?s:.+/)?[^/]+{_DIR_MARK_OPT}'
"""
This regular expression matches every path. It is the expansion of the patterns
"*" and "**" (i.e., "**/{any name}"), and it has to capture the directory marker
like any other pattern so that :class:`.GitIgnoreSpec` can tell a directory
match from a file match.
"""

_POSIX_CLASS_TO_REGEX = {
	# Git's wildmatch implements POSIX bracket character classes using its own
	# ASCII (locale-independent) ``is*`` functions, so each class maps to an
	# explicit ASCII set. These are NOT the Unicode-aware equivalents (``\w``,
	# ``\d``, ``\s``); using those would over-match non-ASCII characters that git
	# never matches.
	'alnum': '0-9A-Za-z',
	'alpha': 'A-Za-z',
	'blank': '\\t ',
	'cntrl': '\\x00-\\x1f\\x7f',
	'digit': '0-9',
	'graph': '\\x21-\\x7e',
	'lower': 'a-z',
	'print': '\\x20-\\x7e',
	'punct': '!-/:-@\\[-`{-~',
	'space': '\\t\\n\\r ',
	'upper': 'A-Z',
	'xdigit': '0-9A-Fa-f',
}
"""
Maps each POSIX bracket character class name to the ASCII regex range that
reproduces git's wildmatch behavior.
"""

_POSIX_CLASS_REGEX = re.compile(r'\[:(\^?)([^:\]]*):\]')
"""
Matches a POSIX bracket character class token such as ``[:alpha:]`` inside a
bracket expression. Group 1 captures a leading caret (unsupported by git);
group 2 captures the class name.
"""


def _translate_posix_class(match: 're.Match') -> str:
	"""
	Translate a single POSIX character class token to its ASCII regex range.
	Raises :class:`_PosixClassError` for a negated (``[:^name:]``) or unknown
	class name, matching git's treatment of it as a malformed pattern.
	"""
	negated, name = match.group(1, 2)
	class_regex = _POSIX_CLASS_TO_REGEX.get(name)
	if negated or class_regex is None:
		raise _PosixClassError((
			f"Invalid character class={match.group(0)!r} found in pattern="
			f"{match.string!r}."
		))  # _PosixClassError

	return class_regex


def translate_segment_glob(
	pattern: str,
	errors: Literal['literal', 'raise'],
) -> str:
	"""
	Translates the glob pattern to a regular expression. This is used in the
	constructor to translate a path segment glob pattern to its corresponding
	regular expression.

	*pattern* (:class:`str`) is the glob pattern.

	*errors* (:class:`str`) is how to handle invalid pattern notation in the
	pattern:

	-	``'literal'``: Invalid notation will be treated as a literal string.

	-	``'raise'``: Invalid notation will raise an exception.

	Raises :exc:`._PosixClassError` when an invalid POXIS class is found and
	*errors* is ``'raise'``.

	Raises :exc:`._RangeNotationError` when an invalid range notation is found
	and *errors* is ``'raise'``.

	Raises :exc:`._TrailingBackslashError` when a trailing backslash is found at
	the end of the pattern, regardless of the value of *errors*.

	Returns the regular expression (:class:`str`).
	"""
	# NOTE: This is derived from `fnmatch.translate()` and is similar to the
	# POSIX function `fnmatch()` with the `FNM_PATHNAME` flag set.

	escape = False
	regex = ''
	i, end = 0, len(pattern)
	while i < end:
		# Get next character.
		char = pattern[i]
		i += 1

		if escape:
			# Escape the character.
			escape = False
			regex += re.escape(char)

		elif char == '\\':
			# Escape character, escape next character.
			escape = True

		elif char == '*':
			# Multi-character wildcard. Match any string (except slashes), including
			# an empty string.
			regex += '[^/]*'

		elif char == '?':
			# Single-character wildcard. Match any single character (except a
			# slash).
			regex += '[^/]'

		elif char == '[':
			# Bracket expression (range notation) wildcard. Except for the beginning
			# exclamation mark, the whole bracket expression can be used directly as
			# regex, but we have to find where the expression ends.
			# - "[][!]" matches ']', '[' and '!'.
			# - "[]-]" matches ']' and '-'.
			# - "[!]a-]" matches any character except ']', 'a' and '-'.
			bracket_start = i - 1
			j = i

			# Pass bracket expression negation.
			if j < end and (pattern[j] == '!' or pattern[j] == '^'):
				j += 1

			# Pass first closing bracket if it is at the beginning of the
			# expression.
			if j < end and pattern[j] == ']':
				j += 1

			# Find closing bracket. Stop once we reach the end or find it.
			while j < end and pattern[j] != ']':
				if pattern[j] == '[' and j + 1 < end and pattern[j + 1] == ':':
					# Skip over a POSIX character class token ("[:name:]") so its
					# internal closing bracket is not mistaken for the end of the whole
					# bracket expression.
					close = pattern.find(':]', j + 2)
					if close == -1:
						j = end
						break
					j = close + 2
				else:
					j += 1

			if j < end:
				# Found end of bracket expression. Increment j to be one past the
				# closing bracket:
				#
				#  [...]
				#   ^   ^
				#   i   j
				#
				j += 1
				expr = '['

				if pattern[i] == '!':
					# Bracket expression needs to be negated.
					expr += '^'
					i += 1
				elif pattern[i] == '^':
					# POSIX declares that the regex bracket expression negation "[^...]"
					# is undefined in a glob pattern. Python's `fnmatch.translate()`
					# escapes the caret ('^') as a literal. Git supports the using a
					# caret for negation. Maintain consistency with Git because that is
					# the expected behavior.
					expr += '^'
					i += 1

				# Build regex bracket expression. Escape slashes so they are treated
				# as literal slashes by regex as defined by POSIX.
				body = pattern[i:j].replace('\\', '\\\\')

				# Translate POSIX character classes (e.g. "[:alpha:]") into their
				# ASCII regex equivalents. Git's wildmatch supports these, but
				# Python's `re` does not, so passing them through verbatim builds a
				# broken regex that silently mismatches (and warns about a nested
				# set).
				try:
					body = _POSIX_CLASS_REGEX.sub(_translate_posix_class, body)
				except _PosixClassError:
					if errors == 'raise':
						# EDGE CASE: Git discards patterns with an invalid range notation
						# or an invalid POSIX class.
						raise
					else:
						# Treat the whole bracket expression as a literal.
						regex += re.escape(pattern[bracket_start:j])
						i = j
						continue

				expr += body

				if errors == 'raise':
					try:
						re.compile(expr)
					except re.error as e:  # Renamed to `re.PatternError` in 3.13.
						raise _RangeNotationError((
							f"Invalid range notation={pattern[i:j]!r} found in "
							f"pattern={pattern!r}."
						)) from e

				# Add regex bracket expression to regex result.
				regex += expr

				# Set i to one past the closing bracket.
				i = j

			else:
				# Failed to find closing bracket.
				if errors == 'raise':
					# Treat invalid range notation as an error.
					raise _RangeNotationError((
						f"Invalid range notation={pattern[i:j]!r} found in {pattern=!r}."
					))
				else:
					# Treat opening bracket as a bracket literal instead of as an
					# expression.
					regex += '\\['

		else:
			# Regular character, escape it for regex.
			regex += re.escape(char)

	if escape:
		# Trailing backslash found. According to the gitignore docs, this "is an
		# invalid pattern that never matches".
		raise _TrailingBackslashError()

	return regex


def translate_segments(
	errors: Literal['literal', 'raise'],
	is_dir_pattern: bool,
	pattern_segs: list,
) -> list:
	"""
	Translate the pattern segments to regular expressions.

	*errors* (:class:`str`) is how to handle invalid pattern notation in the
	pattern:

	-	``'literal'``: Invalid notation will be treated as a literal string.

	-	``'raise'``: Invalid notation will raise an exception.

	*is_dir_pattern* (:class:`bool`) is whether the pattern is a directory
	pattern (i.e., ends with a slash '/').

	*pattern_segs* (:class:`list` of :class:`str`) contains the pattern
	segments.

	Raises :exc:`._PosixClassError` when an invalid POXIS class is found and
	*errors* is ``'literal'``.

	Raises :exc:`._RangeNotationError` when an invalid range notation is found
	and *errors* is ``'literal'``.

	Raises :exc:`._TrailingBackslashError` when a trailing backslash is found at
	the end of the pattern, regardless of the value of *errors*.

	Returns the regular expression parts (:class:`list` of :class:`str`).
	"""
	# Build regular expression from pattern.
	out_parts = []
	need_slash = False
	end = len(pattern_segs) - 1
	for i, seg in enumerate(pattern_segs):
		if seg == '**':
			if i == 0:
				# A normalized pattern beginning with double-asterisks ('**') will
				# match any leading path segments.
				out_parts.append('^(?s:.+/)?')

			elif i < end:
				# A pattern with inner double-asterisks ('**') will match multiple (or
				# zero) inner path segments.
				out_parts.append('(?s:/.+)?')
				need_slash = True

			else:
				assert i == end, (i, end)
				# A normalized pattern ending with double-asterisks ('**') will match
				# nonempty trailing path segments, not the parent directory itself.
				if is_dir_pattern:
					out_parts.append(_DIR_MARK_CG)
				else:
					out_parts.append('/[^/]')

		else:
			# Match path segment.
			if i == 0:
				# Anchor to root directory.
				out_parts.append('^')

			if need_slash:
				out_parts.append('/')

			if seg == '*':
				# Match whole path segment.
				out_parts.append('[^/]+')

			else:
				# Match segment glob pattern.
				# - EDGE CASE: Git discards patterns with invalid range notation.
				out_parts.append(translate_segment_glob(seg, errors))

			if i == end:
				# A pattern ending without a slash ('/') will match a file or a
				# directory (with paths underneath it). E.g., "foo" matches "foo",
				# "foo/bar", "foo/bar/baz", etc.
				out_parts.append(_DIR_MARK_OPT)

			need_slash = True

	return out_parts
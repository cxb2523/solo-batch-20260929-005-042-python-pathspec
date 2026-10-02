"""
This module provides the regular expression fragment assembly component of
the gitignore (gitwildmatch) compile chain. It turns normalized pattern
segments into a list of regular expression parts. It owns the regex
vocabulary shared by the whole chain (the directory-marker group and the
match-all expression) so the normalization and compilation components never
have to rebuild these strings themselves.
"""

from typing import (
	Literal)

from .base import (
	_GitIgnoreBasePattern)

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


def build_regex_parts(
	errors: Literal['literal', 'raise'],
	is_dir_pattern: bool,
	pattern_segs: list[str],
) -> list[str]:
	"""
	Assemble the regular expression parts from the normalized pattern segments.

	*errors* (:class:`str`) is how to handle invalid pattern notation in the
	pattern:

	-	``'literal'``: Invalid notation will be treated as a literal string.

	-	``'raise'``: Invalid notation will raise an exception.

	*is_dir_pattern* (:class:`bool`) is whether the pattern is a directory
	pattern (i.e., ends with a slash '/').

	*pattern_segs* (:class:`list` of :class:`str`) contains the normalized
	pattern segments from :func:`pathspec.patterns.gitignore._normalize.normalize_pattern`.

	Raises :exc:`._PosixClassError` when an invalid POXIS class is found and
	*errors* is ``'raise'``.

	Raises :exc:`._RangeNotationError` when an invalid range notation is found
	and *errors* is ``'raise'``.

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
				out_parts.append(_GitIgnoreBasePattern._translate_segment_glob(seg, errors))

			if i == end:
				# A pattern ending without a slash ('/') will match a file or a
				# directory (with paths underneath it). E.g., "foo" matches "foo",
				# "foo/bar", "foo/bar/baz", etc.
				out_parts.append(_DIR_MARK_OPT)

			need_slash = True

	return out_parts

"""
This module provides common classes for the gitignore patterns.
"""

import re
from typing import (
	Literal,
	Optional,  # Replaced by `X | None` in 3.10.
	Union)  # Replaced by `X | Y` in 3.10.

from pathspec.pattern import (
	RegexPattern)
from pathspec._typing import (
	AnyStr)  # Removed in 3.18.

_BYTES_ENCODING = 'latin1'
"""
The encoding to use when parsing a byte string pattern.
"""


def _strip_trailing_ws(pattern: str) -> str:
	"""
	Strip trailing whitespace from the pattern while considering that whitespace
	can be escaped.

	*pattern* (:class:`str`) is the pattern.

	Returns the modified pattern (:class:`str`).
	"""
	i = len(pattern) - 1
	if i == -1 or not pattern[i].isspace():
		# Fast path: pattern does not end with whitespace. Nothing to strip.
		return pattern

	# Scan past whitespace.
	i -= 1
	while i >= 0 and pattern[i].isspace():
		i -= 1

	last_ws = i + 1

	# Count backslashes.
	while i >= 0 and pattern[i] == '\\':
		i -= 1

	bs_count = last_ws - i - 1
	if bs_count % 2 == 1:
		# Odd count, first whitespace character is escaped, strip the rest.
		last_ws += 1

	# Strip trailing whitespace.
	return pattern[:last_ws]


class _GitIgnoreBasePattern(RegexPattern):
	"""
	.. warning:: This class is not part of the public API. It is subject to
		change.

	The :class:`_GitIgnoreBasePattern` class is the base implementation for a
	compiled gitignore pattern.
	"""

	# Keep the dict-less class hierarchy.
	__slots__ = ()

	def __init__(
		self,
		pattern: Union[AnyStr, re.Pattern, None],
		include: Optional[bool] = None,
		*,
		errors: Optional[Literal['literal', 'null', 'raise']] = None,
	) -> None:
		"""
		Initializes the :class:`_GitIgnoreBasePattern` instance.

		*pattern* (:class:`str`, :class:`bytes`, :class:`re.Pattern`, or
		:data:`None`) is the pattern to compile into a regular expression.

		*include* (:class:`bool` or :data:`None`) must be :data:`None` unless
		*pattern* is a precompiled regular expression (:class:`re.Pattern`) in which
		case it is whether matched files should be included (:data:`True`), excluded
		(:data:`False`), or is a null operation (:data:`None`).

		*errors* (:class:`str` :data:`None`) is how to handle invalid notation in
		the pattern. Default is :data:`None` for ``'null'`` because that is the
		behavior of Git.

		-	``'literal'``: Most invalid notation will be treated as a literal string.
			In cases where the gitignore documentation clearly states the pattern
			should never match, a null-operation will be returned.

		-	``'null'``: Invalid notation will result in a null-operation.

		-	``'raise'``: Invalid notation will raise a :exc:`GitIgnorePatternError`.
		"""
		super().__init__(pattern, include, errors=errors)

	@staticmethod
	def escape(s: AnyStr) -> AnyStr:
		"""
		Escape special characters in the given string.

		*s* (:class:`str` or :class:`bytes`) a filename or a string that you want to
		escape, usually before adding it to a ".gitignore".

		Returns the escaped string (:class:`str` or :class:`bytes`).
		"""
		if isinstance(s, str):
			return_type = str
			string = s
		elif isinstance(s, bytes):
			return_type = bytes
			string = s.decode(_BYTES_ENCODING)
		else:
			raise TypeError(f"s:{s!r} is not a unicode or byte string.")

		# Reference: https://git-scm.com/docs/gitignore#_pattern_format
		out_string = ''.join((f"\\{x}" if x in '\\[]!*#?' else x) for x in string)

		# EDGE CASE: Git strips trailing spaces from a pattern unless they are
		# escaped with a backslash. Escape them so an escaped filename that ends
		# with a space still matches that file.
		stripped = out_string.rstrip(' ')
		trailing = len(out_string) - len(stripped)
		if trailing:
			out_string = stripped + '\\ ' * trailing

		if return_type is bytes:
			out_bytes = out_string.encode(_BYTES_ENCODING)
			return out_bytes  # type: ignore[return-value]
		else:
			return out_string  # type: ignore[return-value]

	@staticmethod
	def _translate_segment_glob(
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
		# The implementation lives in the regex fragment assembly component
		# (pathspec.patterns._gitwildmatch.regex) so that the basic and spec
		# pipelines share a single implementation. This forwarder is kept for
		# backward compatibility. The import is deferred to avoid a circular
		# import at module load time.
		from .._gitwildmatch.regex import (
			translate_segment_glob)
		return translate_segment_glob(pattern, errors)


class GitIgnorePatternError(ValueError):
	"""
	The :class:`GitIgnorePatternError` class indicates an invalid gitignore
	pattern.
	"""
	pass


class _PosixClassError(GitIgnorePatternError):
	"""
	Raised internally when a bracket expression contains an unknown or negated
	POSIX character class name. Git treats such a pattern as malformed.
	"""
	pass


class _RangeNotationError(GitIgnorePatternError):
	"""
	Raised internally when an invalid range notation was found in a gitignore
	pattern.
	"""
	pass


class _TrailingBackslashError(GitIgnorePatternError):
	"""
	Raised internally when a trailing backslash is found at the end of a gitignore
	pattern.
	"""
	pass
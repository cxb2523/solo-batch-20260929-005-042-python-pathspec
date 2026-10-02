"""
This module provides :class:`GitIgnoreSpecPattern` which implements Git's
`gitignore`_ patterns, and handles edge-cases where Git's behavior differs from
what's documented. Git allows including files from excluded directories which
appears to contradict the documentation. Git discards patterns with invalid
range notation. This is used by :class:`~pathspec.gitignore.GitIgnoreSpec` to
fully replicate Git's handling.

The compile chain is split into components, each in its own module:

- :mod:`pathspec.patterns.gitignore._normalize` normalizes the pattern
(negation prefix, trailing slash, anchoring and root-relative segments,
``**`` segments).

- :mod:`pathspec.patterns.gitignore._fragments` assembles the regular
expression parts from the normalized segments.

- :mod:`pathspec.patterns.gitignore._compile` compiles the regular expression
and forwards matching to it.

- :mod:`pathspec.patterns.gitignore._cache` is the thread-safe regex cache
used by the compilation component.

.. _`gitignore`: https://git-scm.com/docs/gitignore
"""

import re
from collections.abc import (
	Iterable,
	Iterator)
from typing import (
	Literal,
	Optional)  # Replaced by `X | None` in 3.10.

from pathspec._typing import (
	AnyStr,  # Removed in 3.18.
	assert_unreachable,
	override)  # Added in 3.12.
from pathspec.pattern import (
	RegexMatchResult)

from . import (
	_compile)
from .base import (
	GitIgnorePatternError,
	_BYTES_ENCODING,
	_GitIgnoreBasePattern,
	_PosixClassError,
	_RangeNotationError,
	_TrailingBackslashError)
from ._fragments import (
	_DIR_MARK,
	_DIR_MARK_CG,
	_DIR_MARK_OPT,
	_MATCH_ALL,
	build_regex_parts)
from ._normalize import (
	NormalizedPattern,
	normalize_pattern)

# Re-exported from `._fragments` for backward compatibility. These names have
# always been importable from this module (used by the "hyperscan" and "re2"
# backends, the benchmarks, and the tests).
__all__ = [
	'GitIgnoreSpecPattern',
]


class GitIgnoreSpecPattern(_GitIgnoreBasePattern):
	"""
	The :class:`GitIgnoreSpecPattern` class represents a compiled gitignore
	pattern with special handling for edge-cases to replicate Git's behavior.

	This is registered under the deprecated name "gitwildmatch" for backward
	compatibility with v0.12. The registered name will be removed in a future
	version.
	"""

	# Keep the dict-less class hierarchy.
	__slots__ = ()

	@override
	@classmethod
	def _compile_regex(cls, raw_regex: AnyStr) -> re.Pattern:
		"""
		Compile the regular expression through the regex cache component
		(:mod:`pathspec.patterns.gitignore._compile`) so repeated patterns are
		compiled at most once, process-wide and thread-safe.
		"""
		return _compile.compile_regex(raw_regex)

	@override
	def match_file(self, file: AnyStr) -> Optional[RegexMatchResult]:
		"""
		Matches this pattern against the specified file. This only forwards to
		the matching component
		(:func:`pathspec.patterns.gitignore._compile.match_file`).

		*file* (:class:`str` or :class:`bytes`) is the file path relative to the
		root directory (e.g., "relative/path/to/file").

		Returns the match result (:class:`.RegexMatchResult`) if *file* matched;
		otherwise, :data:`None`.
		"""
		return _compile.match_file(self.regex, file)

	def match_files(self, files: Iterable[AnyStr]) -> Iterator[AnyStr]:
		"""
		Matches this pattern against each of the specified files. This only
		forwards to the matching component
		(:func:`pathspec.patterns.gitignore._compile.match_files`).

		*files* (:class:`~collections.abc.Iterable` of :class:`str`) contains
		each file relative to the root directory.

		Returns an :class:`~collections.abc.Iterator` yielding each matched file
		path (:class:`str`).
		"""
		return _compile.match_files(self.regex, files)

	@override
	@classmethod
	def pattern_to_regex(
		cls,
		pattern: AnyStr,
		*,
		errors: Optional[Literal['literal', 'null', 'raise']] = None,
	) -> tuple[Optional[AnyStr], Optional[bool]]:
		"""
		Convert the pattern into a regular expression. This composes the
		normalization component
		(:func:`pathspec.patterns.gitignore._normalize.normalize_pattern`) with
		the fragment assembly component
		(:func:`pathspec.patterns.gitignore._fragments.build_regex_parts`).

		*pattern* (:class:`str` or :class:`bytes`) is the pattern to convert into a
		regular expression.

		*errors* (:class:`str` :data:`None`) is how to handle invalid notation in
		the pattern. Default is :data:`None` for ``'null'`` because that is the
		behavior of Git.

		-	``'literal'``: Most invalid notation will be treated as a literal
			string. In cases where the gitignore documentation clearly states the
			pattern should never match, a null-operation will be returned.

		-	``'null'``: Invalid notation will result in a null-operation.

		-	``'raise'``: Invalid notation will raise a :exc:`.GitIgnorePatternError`.

		Raises :exc:`.GitIgnorePatternError` if the pattern fails to process,
		regardless of the value of *errors*.

		Returns a :class:`tuple` containing:

		-	*pattern* (:class:`str`, :class:`bytes` or :data:`None`) is the uncompiled
			regular expression.

		-	*include* (:class:`bool` or :data:`None`) is whether matched files should
			be included (:data:`True`), excluded (:data:`False`), or is a
			null-operation (:data:`None`).
		"""
		if isinstance(pattern, str):
			pattern_str = pattern
			return_type = str
		elif isinstance(pattern, bytes):
			pattern_str = pattern.decode(_BYTES_ENCODING)
			return_type = bytes
		else:
			raise TypeError(f"{pattern=!r} is not a unicode or byte string.")

		original_pattern = pattern_str
		del pattern

		if errors is None:
			errors = 'null'
		elif errors not in ('literal', 'null', 'raise'):
			raise ValueError(f"{errors=!r} is not a valid value.")

		seg_errors: Literal['literal', 'raise']
		if errors == 'null':
			seg_errors = 'raise'
		elif errors in ('literal', 'raise'):
			seg_errors = errors
		else:
			assert_unreachable(f"Failed to map {errors=!r} to seg_errors.")

		# Normalize the pattern (component 1: negation prefix, trailing slash,
		# anchoring, root-relative and "**" segments).
		try:
			norm: NormalizedPattern = normalize_pattern(pattern_str)
		except ValueError as e:
			raise GitIgnorePatternError((
				f"Invalid git pattern: {original_pattern!r}"
			)) from e  # GitIgnorePatternError

		if norm.include is None:
			# A null-operation (comment, blank, or "/") neither includes nor
			# excludes files.
			return (None, None)

		include = norm.include

		regex: Optional[str]
		if norm.regex_override is not None:
			# Use regex override.
			regex = norm.regex_override

		elif norm.segments is not None:
			# Build regular expression from pattern (component 2: fragment
			# assembly).
			try:
				regex_parts = build_regex_parts(
					seg_errors, norm.is_dir_pattern, norm.segments,
				)
			except (_PosixClassError, _RangeNotationError) as e:
				if errors == 'raise':
					raise GitIgnorePatternError((
						f"Invalid git pattern: {original_pattern!r}"
					)) from e  # GitIgnorePatternError
				else:
					return (None, None)
			except _TrailingBackslashError as e:
				# EDGE CASE: The gitignore docs say a trailing backlash is invalid and
				# never matches.
				if errors == 'raise':
					raise GitIgnorePatternError((
						f"Invalid git pattern: {original_pattern!r}"
					)) from e  # GitIgnorePatternError
				else:
					return (None, None)

			regex = ''.join(regex_parts)

		else:
			assert_unreachable((
				f"{norm.regex_override=} and {norm.segments=} cannot both be null."
			))  # assert_unreachable

		# Encode regex if needed.
		out_regex: AnyStr
		if regex is not None and return_type is bytes:
			regex_bytes = regex.encode(_BYTES_ENCODING)
			out_regex = regex_bytes  # type: ignore[assignment]
		else:
			out_regex = regex  # type: ignore[assignment]

		return (out_regex, include)

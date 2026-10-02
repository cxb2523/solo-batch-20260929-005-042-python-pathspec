"""
This module provides :class:`GitIgnoreSpecPattern` which implements Git's
`gitignore`_ patterns, and handles edge-cases where Git's behavior differs from
what's documented. Git allows including files from excluded directories which
appears to contradict the documentation. Git discards patterns with invalid
range notation. This is used by :class:`~pathspec.gitignore.GitIgnoreSpec` to
fully replicate Git's handling.

The compile pipeline (normalization, regex fragment assembly, compilation, and
the regex cache) lives in the :mod:`pathspec.patterns._gitwildmatch` components.
This class only forwards to them.

.. _`gitignore`: https://git-scm.com/docs/gitignore
"""

import re
from typing import (
	Literal,
	Optional,  # Replaced by `X | None` in 3.10.
	Union)  # Replaced by `X | Y` in 3.10.

from pathspec._typing import (
	AnyStr,  # Removed in 3.18.
	override)  # Added in 3.12.

from .._gitwildmatch.compile import (
	compile_pattern as _compile_pattern,
	translate_pattern as _translate_pattern)
from .._gitwildmatch.regex import (
	_DIR_MARK,
	_DIR_MARK_CG,
	_DIR_MARK_OPT,
	_MATCH_ALL)
from .base import (
	GitIgnorePatternError,
	_BYTES_ENCODING,
	_GitIgnoreBasePattern,
	_PosixClassError,
	_RangeNotationError,
	_TrailingBackslashError,
	_strip_trailing_ws)

# Re-exported for backward compatibility: these names used to be defined in
# this module and are still imported from here (e.g., by the test-suite and
# the simple backend).
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
	def __init__(
		self,
		pattern: Union[AnyStr, re.Pattern, None],
		include: Optional[bool] = None,
		*,
		errors: Optional[Literal['literal', 'null', 'raise']] = None,
	) -> None:
		"""
		Initializes the :class:`GitIgnoreSpecPattern` instance.

		*pattern* (:class:`str`, :class:`bytes`, :class:`re.Pattern`, or
		:data:`None`) is the pattern to compile into a regular expression.

		*include* (:class:`bool` or :data:`None`) must be :data:`None` unless
		*pattern* is a precompiled regular expression (:class:`re.Pattern`) in
		which case it is whether matched files should be included
		(:data:`True`), excluded (:data:`False`), or is a null operation
		(:data:`None`).

		*errors* (:class:`str` or :data:`None`) is how to handle invalid
		notation in the pattern. Default is :data:`None` for ``'null'``.
		"""
		if isinstance(pattern, (str, bytes)):
			assert include is None, (
				f"{include=!r} must be null when {pattern=!r} is a string."
			)
			# The whole compile pipeline (normalization, fragment assembly,
			# compilation, caching) lives in the _gitwildmatch components.
			entry = _compile_pattern(pattern, errors=errors)
			super().__init__(entry.compiled, entry.include)
			# Keep a reference to the original pattern (same as before the
			# pipeline was split out).
			self.pattern = pattern

		else:
			super().__init__(pattern, include, errors=errors)

	@override
	@classmethod
	def pattern_to_regex(
		cls,
		pattern: AnyStr,
		*,
		errors: Optional[Literal['literal', 'null', 'raise']] = None,
	) -> tuple[Optional[AnyStr], Optional[bool]]:
		"""
		Convert the pattern into a regular expression.

		*pattern* (:class:`str` or :class:`bytes`) is the pattern to convert into
		a regular expression.

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
		# Only forwards to the compile component.
		return _translate_pattern(pattern, errors=errors)
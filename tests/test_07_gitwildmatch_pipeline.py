"""
This script tests the gitwildmatch compile pipeline components
(:mod:`pathspec.patterns._gitwildmatch`) and the forwarding behavior of
:class:`.GitWildMatchPattern`.
"""

import threading
import unittest
import warnings

from pathspec.patterns._gitwildmatch.cache import (
	RegexCache)
from pathspec.patterns._gitwildmatch.compile import (
	compile_count,
	compile_pattern,
	translate_pattern)
from pathspec.patterns._gitwildmatch.normalize import (
	is_anchored,
	normalize_pattern)
from pathspec.patterns.gitignore.base import (
	GitIgnorePatternError)
from pathspec.patterns.gitignore.spec import (
	GitIgnoreSpecPattern)
from pathspec.patterns.gitwildmatch import (
	GitWildMatchPattern)


class NormalizeComponentTest(unittest.TestCase):
	"""
	Tests the normalization component.
	"""

	def test_negation_prefix(self):
		"""
		Tests that the '!' prefix flips the include flag.
		"""
		norm = normalize_pattern('!foo')
		self.assertIs(norm.include, False)
		self.assertEqual(norm.segments, ['**', 'foo'])

	def test_trailing_slash_dir_pattern(self):
		"""
		Tests that a trailing slash marks a directory pattern.
		"""
		norm = normalize_pattern('foo/')
		self.assertTrue(norm.is_dir_pattern)
		self.assertEqual(norm.segments, ['**', 'foo', '**'])

	def test_anchored_root(self):
		"""
		Tests that a leading slash anchors the pattern to the root.
		"""
		norm = normalize_pattern('/foo/bar')
		self.assertTrue(norm.anchored)
		self.assertEqual(norm.segments, ['foo', 'bar'])

	def test_relative_single_segment(self):
		"""
		Tests that a single segment pattern is relative to any depth.
		"""
		norm = normalize_pattern('foo')
		self.assertFalse(norm.anchored)
		self.assertEqual(norm.segments, ['**', 'foo'])

	def test_double_star_collapse(self):
		"""
		Tests that duplicate '**' segments are collapsed.
		"""
		norm = normalize_pattern('a/**/**/b')
		self.assertEqual(norm.segments, ['a', '**', 'b'])

	def test_null_operations(self):
		"""
		Tests that comments, blanks, and '/' are null-operations.
		"""
		self.assertIsNone(normalize_pattern('# comment'))
		self.assertIsNone(normalize_pattern('!'))
		self.assertIsNone(normalize_pattern('/'))

	def test_anchor_posix_windows_consistent(self):
		"""
		Tests that anchor detection normalizes separators first, so the POSIX
		and Windows flavors of the same logical root path agree.
		"""
		for posix, windows in [
			('/repo/src', 'C:\\repo\\src'),
			('/repo/src', '\\repo\\src'),
			('repo/src', 'repo\\src'),
			('src', 'src'),
		]:
			with self.subTest(posix=posix, windows=windows):
				self.assertEqual(
					is_anchored(posix, 'posix'),
					is_anchored(windows, 'windows'),
				)

	def test_anchor_invalid_flavor(self):
		"""
		Tests that an invalid flavor raises.
		"""
		with self.assertRaises(ValueError):
			is_anchored('foo', 'plan9')


class CacheComponentTest(unittest.TestCase):
	"""
	Tests the regex cache component.
	"""

	def test_thread_safe_single_compile(self):
		"""
		Tests that the same key is compiled exactly once even when hit from
		many threads at once.
		"""
		cache = RegexCache()
		compile_calls = []
		calls_lock = threading.Lock()

		def compile_fn():
			with calls_lock:
				compile_calls.append(1)
			return object()

		def worker():
			cache.get_or_compile('key', compile_fn)

		threads = [threading.Thread(target=worker) for _ in range(16)]
		for thread in threads:
			thread.start()
		for thread in threads:
			thread.join()

		self.assertEqual(len(compile_calls), 1)
		self.assertEqual(cache.compile_count('key'), 1)

	def test_shared_cache_single_compile(self):
		"""
		Tests that the shared cache compiles a raw pattern only once across
		the component and the pattern class.
		"""
		pattern = 'some/unique-pipeline-test-*.xyz'
		before = compile_count(pattern)

		entry1 = compile_pattern(pattern)
		entry2 = compile_pattern(pattern)
		pat = GitIgnoreSpecPattern(pattern)

		self.assertIs(entry1, entry2)
		self.assertIs(pat.regex, entry1.compiled)
		self.assertEqual(compile_count(pattern), before + 1)

	def test_cache_key_is_raw_pattern(self):
		"""
		Tests that the cache key is the raw pattern string: equivalent
		patterns with different raw strings are separate entries.
		"""
		compile_pattern('**')
		compile_pattern('**/**')
		self.assertEqual(compile_count('**'), 1)
		self.assertEqual(compile_count('**/**'), 1)


class CompileComponentTest(unittest.TestCase):
	"""
	Tests the compile component and its integration with the pattern class.
	"""

	def test_translate_matches_pattern_to_regex(self):
		"""
		Tests that the component and the classmethod agree.
		"""
		for pattern in ('*.py', '/foo/bar', '!temp', 'spam/**', 'left/**/right'):
			with self.subTest(pattern=pattern):
				self.assertEqual(
					translate_pattern(pattern),
					GitIgnoreSpecPattern.pattern_to_regex(pattern),
				)

	def test_pattern_attribute_preserved(self):
		"""
		Tests that the original pattern reference is preserved.
		"""
		pattern = GitIgnoreSpecPattern('foo/*.py')
		self.assertEqual(pattern.pattern, 'foo/*.py')
		self.assertTrue(pattern.include)

	def test_invalid_range_error_message(self):
		"""
		Tests that the exception type and message for invalid patterns are
		preserved.
		"""
		with self.assertRaises(GitIgnorePatternError) as ctx:
			GitIgnoreSpecPattern('a[', errors='raise')
		self.assertEqual(str(ctx.exception), "Invalid git pattern: 'a['")

	def test_trailing_backslash_error_message(self):
		"""
		Tests that the exception type and message for a trailing backslash
		are preserved.
		"""
		with self.assertRaises(GitIgnorePatternError) as ctx:
			GitIgnoreSpecPattern('foo\\', errors='raise')
		self.assertEqual(str(ctx.exception), "Invalid git pattern: 'foo\\\\'")

	def test_invalid_errors_value(self):
		"""
		Tests that an invalid errors mode raises, even on a cached pattern.
		"""
		compile_pattern('cached-pattern-for-errors-test')
		with self.assertRaises(ValueError):
			compile_pattern('cached-pattern-for-errors-test', errors='bogus')


class ForwardingTest(unittest.TestCase):
	"""
	Tests that GitWildMatchPattern matching only forwards.
	"""

	def _make_pattern(self, pattern):
		with warnings.catch_warnings():
			warnings.simplefilter('ignore', DeprecationWarning)
			return GitWildMatchPattern(pattern)

	def test_match_files_forwards_to_match_file(self):
		"""
		Tests that match_files() yields exactly what match_file() matches.
		"""
		pattern = self._make_pattern('*.py')
		files = ['a.py', 'b.txt', 'dir/c.py']
		expected = [f for f in files if pattern.match_file(f) is not None]
		self.assertEqual(list(pattern.match_files(files)), expected)
		self.assertEqual(expected, ['a.py', 'dir/c.py'])

	def test_match_file_result(self):
		"""
		Tests that match_file() forwards to the compiled regex.
		"""
		pattern = self._make_pattern('/foo/bar')
		self.assertIsNotNone(pattern.match_file('foo/bar'))
		self.assertIsNone(pattern.match_file('baz/foo/bar'))
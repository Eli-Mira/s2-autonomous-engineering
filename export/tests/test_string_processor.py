"""Acceptance tests for StringProcessor - PROTECTED, do not modify."""

import pytest
from src.string_processor import StringProcessor


class TestStringProcessor:
    """Test suite for StringProcessor module."""
    
    def test_reverse_string(self):
        """Test string reversal."""
        sp = StringProcessor()
        assert sp.reverse("hello") == "olleh"
        assert sp.reverse("") == ""
        assert sp.reverse("a") == "a"
        assert sp.reverse("Python") == "nohtyP"
    
    def test_word_count(self):
        """Test word counting."""
        sp = StringProcessor()
        assert sp.word_count("hello world") == 2
        assert sp.word_count("") == 0
        assert sp.word_count("one") == 1
        assert sp.word_count("  multiple   spaces  ") == 2
    
    def test_is_palindrome(self):
        """Test palindrome detection."""
        sp = StringProcessor()
        assert sp.is_palindrome("racecar") == True
        assert sp.is_palindrome("level") == True
        assert sp.is_palindrome("hello") == False
        assert sp.is_palindrome("") == True
        assert sp.is_palindrome("A") == True
        assert sp.is_palindrome("Madam") == True  # case insensitive
    
    def test_most_common_char(self):
        """Test most common character."""
        sp = StringProcessor()
        assert sp.most_common_char("hello") == "l"
        assert sp.most_common_char("aabbbc") == "b"
        assert sp.most_common_char("") is None
    
    def test_remove_vowels(self):
        """Test vowel removal."""
        sp = StringProcessor()
        assert sp.remove_vowels("hello world") == "hll wrld"
        assert sp.remove_vowels("PYTHON") == "PYTHN"
        assert sp.remove_vowels("") == ""
        assert sp.remove_vowels("aeiou") == ""

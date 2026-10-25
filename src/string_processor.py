import re
from collections import Counter
import math

class StringProcessor:
    def reverse(self, text: str) -> str:
        return text[::-1]

    def word_count(self, text: str) -> int:
        if not text or not text.strip():
            return 0
        return len(text.split())

    def is_palindrome(self, text: str) -> bool:
        cleaned = re.sub(r'[^a-zA-Z0-9]', '', text).lower()
        return cleaned == cleaned[::-1]

    def most_common_char(self, text: str) -> str | None:
        cleaned = [c.lower() for c in text if c.isalnum()]
        if not cleaned:
            return None
        counts = Counter(cleaned)
        max_count = max(counts.values())
        for char, count in counts.items():
            if count == max_count:
                return char
        return None

    def remove_vowels(self, text: str) -> str:
        vowels = set('aeiouAEIOU')
        return ''.join(c for c in text if c not in vowels)

    def char_frequency(self, text: str) -> dict[str, int]:
        cleaned = [c.lower() for c in text if c.isalnum()]
        return dict(Counter(cleaned))

    def word_frequency(self, text: str) -> dict[str, int]:
        words = text.split()
        return dict(Counter(words))

    def entropy(self, text: str) -> float:
        cleaned = [c.lower() for c in text if c.isalnum()]
        if not cleaned:
            return 0.0
        total = len(cleaned)
        counts = Counter(cleaned)
        return -sum((count / total) * math.log2(count / total) for count in counts.values())

    def longest_word(self, text: str) -> str:
        words = text.split()
        if not words:
            return ""
        return max(words, key=len)

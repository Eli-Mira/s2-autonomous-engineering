import re
import math
from collections import Counter

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
        return dict(Counter(text.split()))

    def entropy(self, text: str) -> float:
        cleaned = [c.lower() for c in text if c.isalnum()]
        if not cleaned:
            return 0.0
        
        total = len(cleaned)
        counts = Counter(cleaned)
        
        entropy_val = 0.0
        for count in counts.values():
            prob = count / total
            if prob > 0:
                entropy_val -= prob * math.log2(prob)
        return entropy_val

    def longest_word(self, text: str) -> str:
        words = text.split()
        return max(words, key=len) if words else ""

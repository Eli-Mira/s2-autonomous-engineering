from collections import Counter

class StringProcessor:
    def reverse(self, text: str) -> str:
        return text[::-1]

    def word_count(self, text: str) -> int:
        return len(text.split()) if text.strip() else 0

    def is_palindrome(self, text: str) -> bool:
        cleaned = text.lower().replace(" ", "")
        return cleaned == cleaned[::-1]

    def most_common_char(self, text: str):
        if not text:
            return None
        counts = Counter(text)
        return counts.most_common(1)[0][0]

    def remove_vowels(self, text: str) -> str:
        vowels = set("aeiouAEIOU")
        return "".join(char for char in text if char not in vowels)

"""Password policy shared by registration, reset and change-password flows."""

from __future__ import annotations

import re

MIN_LENGTH = 8
MAX_LENGTH = 128

# A compact deny-list of the most common passwords that still satisfy the
# letter + digit rule. Larger lists belong in a breached-password service.
COMMON_PASSWORDS = frozenset(
    {
        "password1",
        "password12",
        "password123",
        "password1234",
        "passw0rd",
        "p@ssw0rd",
        "abc12345",
        "abcd1234",
        "qwerty123",
        "qwerty12",
        "qwerty1234",
        "1qaz2wsx",
        "iloveyou1",
        "welcome1",
        "welcome123",
        "letmein1",
        "letmein123",
        "admin123",
        "admin1234",
        "administrator1",
        "monkey123",
        "dragon123",
        "football1",
        "baseball1",
        "sunshine1",
        "princess1",
        "trustno1",
        "superman1",
        "michael1",
        "jordan23",
        "a1b2c3d4",
        "aa123456",
        "zaq12wsx",
        "123qweasd",
        "qwe12345",
        "asdf1234",
        "zxcv1234",
        "changeme1",
        "test1234",
        "testing123",
        "secret123",
        "master123",
        "hello123",
        "freedom1",
        "whatever1",
        "computer1",
        "internet1",
        "starwars1",
        "pokemon1",
        "1q2w3e4r",
        "1q2w3e4r5t",
        "q1w2e3r4",
        "summer2024",
        "winter2024",
        "spring2024",
        "autumn2024",
        "summer2025",
        "winter2025",
        "summer2026",
        "winter2026",
        "sleeptrack1",
        "sleeptrack123",
        "123456abc",
        "123abc123",
        "abc123456",
        "password2024",
        "password2025",
        "password2026",
    }
)

_LETTER = re.compile(r"[A-Za-z]")
_DIGIT = re.compile(r"\d")


def password_problems(password: str) -> list[str]:
    problems: list[str] = []
    if not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        problems.append(f"must be between {MIN_LENGTH} and {MAX_LENGTH} characters")
    if not _LETTER.search(password) or not _DIGIT.search(password):
        problems.append("must contain at least one letter and one digit")
    if password.lower() in COMMON_PASSWORDS:
        problems.append("is too common; choose something less guessable")
    return problems


def validate_password(password: str) -> str:
    problems = password_problems(password)
    if problems:
        raise ValueError(f"password {'; '.join(problems)}")
    return password

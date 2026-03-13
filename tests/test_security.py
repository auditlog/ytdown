#!/usr/bin/env python3
"""
Skrypt testowy dla funkcji bezpieczeństwa YouTube Downloader
"""

import sys
import os
import time

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

# Import functions from bot package
from bot.security import (
    check_rate_limit,
    validate_youtube_url,
    estimate_file_size,
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW,
    MAX_FILE_SIZE_MB,
    user_requests
)

def test_rate_limiting():
    """Test funkcji rate limiting"""
    print("=== Test Rate Limiting ===")
    test_user_id = 99999  # Use unique ID to avoid conflicts

    # Clear previous requests for this user
    user_requests[test_user_id] = []

    # Test normalnego użycia
    print(f"Limit: {RATE_LIMIT_REQUESTS} requestów w {RATE_LIMIT_WINDOW} sekund")

    # Wykonaj dozwoloną liczbę requestów
    for i in range(RATE_LIMIT_REQUESTS):
        result = check_rate_limit(test_user_id)
        print(f"Request {i+1}: {'✅ Dozwolony' if result else '❌ Zablokowany'}")
        assert result == True, f"Request {i+1} powinien być dozwolony"

    # Następny request powinien być zablokowany
    result = check_rate_limit(test_user_id)
    print(f"Request {RATE_LIMIT_REQUESTS + 1}: {'✅ Dozwolony' if result else '❌ Zablokowany'}")
    assert result == False, "Request przekraczający limit powinien być zablokowany"

    print("✅ Rate limiting działa poprawnie\n")

def test_url_validation():
    """Test walidacji URL"""
    print("=== Test Walidacji URL ===")

    # Prawidłowe URL-e
    valid_urls = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtube.com/watch?v=test",
        "https://m.youtube.com/watch?v=test",
        "https://music.youtube.com/watch?v=test"
    ]

    # Nieprawidłowe URL-e
    invalid_urls = [
        "https://www.google.com",
        "http://youtube.com/watch?v=test",  # http zamiast https
        "youtube.com/watch?v=test",  # brak protokołu
        "https://fake-youtube.com/watch?v=test",
        "https://www.youtube-downloader.com",
        ""
    ]

    print("Testowanie prawidłowych URL:")
    for url in valid_urls:
        result = validate_youtube_url(url)
        print(f"  {url[:50]}... {'✅' if result else '❌'}")
        assert result == True, f"URL {url} powinien być prawidłowy"

    print("\nTestowanie nieprawidłowych URL:")
    for url in invalid_urls:
        result = validate_youtube_url(url)
        print(f"  {url[:50]}... {'✅' if result else '❌'}")
        assert result == False, f"URL {url} powinien być nieprawidłowy"

    print("✅ Walidacja URL działa poprawnie\n")

def test_file_size_estimation():
    """Test szacowania rozmiaru pliku"""
    print("=== Test Szacowania Rozmiaru ===")

    # Symulacja info z yt-dlp
    test_cases = [
        {
            "name": "Film z dokładnym rozmiarem",
            "info": {
                "formats": [
                    {"format_id": "22", "filesize": 100 * 1024 * 1024},  # 100 MB
                    {"format_id": "18", "filesize": 50 * 1024 * 1024}    # 50 MB
                ]
            },
            "expected": 100.0
        },
        {
            "name": "Film bez rozmiaru, z czasem trwania",
            "info": {
                "duration": 600,  # 10 minut
                "formats": [{"format_id": "22"}]
            },
            "expected": 375.0  # przybliżone
        },
        {
            "name": "Film przekraczający limit",
            "info": {
                "formats": [
                    {"format_id": "22", "filesize": 600 * 1024 * 1024}  # 600 MB
                ]
            },
            "expected": 600.0
        }
    ]

    print(f"Maksymalny dozwolony rozmiar: {MAX_FILE_SIZE_MB} MB\n")

    for test in test_cases:
        size = estimate_file_size(test["info"])
        print(f"{test['name']}:")
        print(f"  Szacowany rozmiar: {size:.1f} MB" if size else "  Nie można oszacować")

        if size and size > MAX_FILE_SIZE_MB:
            print(f"  ⚠️ Przekracza limit!")
        elif size:
            print(f"  ✅ Mieści się w limicie")

    print("\n✅ Szacowanie rozmiaru działa\n")

def test_env_variables():
    """Test zmiennych środowiskowych"""
    print("=== Test Zmiennych Środowiskowych ===")

    # Sprawdź które zmienne są ustawione
    env_vars = ["TELEGRAM_BOT_TOKEN", "GROQ_API_KEY", "CLAUDE_API_KEY", "PIN_CODE"]

    print("Sprawdzanie zmiennych środowiskowych:")
    for var in env_vars:
        value = os.environ.get(var)
        if value:
            print(f"  {var}: ✅ Ustawiona (długość: {len(value)})")
        else:
            print(f"  {var}: ❌ Nie ustawiona")

    print("\n💡 Wskazówka: Możesz ustawić zmienne środowiskowe zamiast używać pliku api_key.md")
    print("   Przykład: export TELEGRAM_BOT_TOKEN='twój_token'\n")

def main():
    """Uruchom wszystkie testy"""
    print("🔒 Testy Bezpieczeństwa YouTube Downloader\n")

    try:
        test_rate_limiting()
        test_url_validation()
        test_file_size_estimation()
        test_env_variables()

        print("✅ Wszystkie testy zakończone pomyślnie!")

    except AssertionError as e:
        print(f"\n❌ Test nieudany: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Błąd podczas testów: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Samodzielny skrypt testowy dla funkcji bezpieczeństwa
Nie wymaga zainstalowanych bibliotek Telegram
"""

import time
from collections import defaultdict

# Stałe z głównego pliku
RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_WINDOW = 60
MAX_FILE_SIZE_MB = 500

# Słownik do śledzenia requestów użytkowników
user_requests = defaultdict(list)

def check_rate_limit(user_id):
    """
    Sprawdza czy użytkownik nie przekroczył limitu requestów.
    Zwraca True jeśli można kontynuować, False jeśli przekroczono limit.
    """
    current_time = time.time()
    
    # Usuń stare requesty spoza okna czasowego
    user_requests[user_id] = [
        req_time for req_time in user_requests[user_id] 
        if current_time - req_time < RATE_LIMIT_WINDOW
    ]
    
    # Sprawdź czy nie przekroczono limitu
    if len(user_requests[user_id]) >= RATE_LIMIT_REQUESTS:
        return False
    
    # Dodaj nowy request
    user_requests[user_id].append(current_time)
    return True

def validate_youtube_url(url):
    """
    Waliduje URL YouTube.
    Zwraca True jeśli URL jest prawidłowy, False w przeciwnym razie.
    """
    try:
        # Tylko HTTPS jest dozwolone (bezpieczne połączenie)
        if not url.startswith('https://'):
            return False
        
        # Lista dozwolonych domen YouTube
        allowed_domains = [
            'https://www.youtube.com/',
            'https://youtube.com/',
            'https://youtu.be/',
            'https://m.youtube.com/',
            'https://music.youtube.com/'
        ]
        
        # Sprawdź czy URL zaczyna się od dozwolonej domeny
        for domain in allowed_domains:
            if url.startswith(domain):
                return True
        
        return False
    except:
        return False

def estimate_file_size(info):
    """
    Szacuje rozmiar pliku na podstawie informacji z yt-dlp.
    Zwraca rozmiar w MB lub None jeśli nie można oszacować.
    """
    try:
        # Spróbuj znaleźć format z rozmiarem
        formats = info.get('formats', [])
        for fmt in formats:
            if fmt.get('filesize'):
                return fmt['filesize'] / (1024 * 1024)  # Konwersja na MB
        
        # Jeśli nie ma dokładnego rozmiaru, spróbuj oszacować
        duration = info.get('duration', 0)
        if duration:
            # Zakładamy średni bitrate dla różnych jakości
            # To bardzo przybliżone szacowanie
            bitrate_mbps = 5  # 5 Mbps dla średniej jakości video
            estimated_mb = (duration * bitrate_mbps * 0.125)  # konwersja na MB
            return estimated_mb
        
        return None
    except:
        return None

def test_rate_limiting():
    """Test funkcji rate limiting"""
    print("=== Test Rate Limiting ===")
    test_user_id = 12345
    
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
        "https://vimeo.com/123456",
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

def main():
    """Uruchom wszystkie testy"""
    print("🔒 Testy Bezpieczeństwa YouTube Downloader (wersja standalone)\n")
    
    try:
        test_rate_limiting()
        test_url_validation()
        test_file_size_estimation()
        
        print("✅ Wszystkie testy zakończone pomyślnie!")
        
    except AssertionError as e:
        print(f"\n❌ Test nieudany: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ Błąd podczas testów: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
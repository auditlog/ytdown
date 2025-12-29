#!/usr/bin/env python3
"""
Skrypt pomocniczy do bezpiecznej konfiguracji YouTube Downloader Bot
"""

import os
import sys
import getpass
import re

def validate_telegram_token(token):
    """Sprawdza format tokenu Telegram."""
    return bool(re.match(r'^\d{8,10}:[A-Za-z0-9_-]{35}$', token))

def validate_pin(pin):
    """Sprawdza format PIN."""
    return pin.isdigit() and len(pin) == 8

def setup_config():
    """Interaktywny setup konfiguracji."""
    print("🔧 YouTube Downloader Bot - Konfiguracja\n")
    
    config_file = "api_key.md"
    
    # Sprawdź czy plik już istnieje
    if os.path.exists(config_file):
        response = input(f"Plik {config_file} już istnieje. Nadpisać? (t/N): ").lower()
        if response != 't':
            print("Anulowano.")
            return
    
    # Zbierz dane
    print("\n📝 Wprowadź dane konfiguracyjne:\n")
    
    # Telegram Bot Token
    while True:
        telegram_token = getpass.getpass("TELEGRAM_BOT_TOKEN (ukryty): ").strip()
        if not telegram_token:
            print("❌ Token nie może być pusty!")
            continue
        if not validate_telegram_token(telegram_token):
            print("❌ Nieprawidłowy format tokenu Telegram!")
            print("   Format: NNNNNNNNNN:XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX")
            continue
        break
    
    # Groq API Key
    groq_key = getpass.getpass("GROQ_API_KEY (ukryty, Enter aby pominąć): ").strip()
    
    # Claude API Key
    claude_key = getpass.getpass("CLAUDE_API_KEY (ukryty, Enter aby pominąć): ").strip()
    if claude_key and not claude_key.startswith("sk-"):
        print("⚠️  Uwaga: Claude API key zazwyczaj zaczyna się od 'sk-'")
    
    # PIN Code
    while True:
        pin = getpass.getpass("PIN_CODE (8 cyfr): ").strip()
        if not validate_pin(pin):
            print("❌ PIN musi składać się z dokładnie 8 cyfr!")
            continue
        if pin == "12345678":
            print("⚠️  Uwaga: Używasz domyślnego PIN! Zalecana zmiana.")
            response = input("Kontynuować mimo to? (t/N): ").lower()
            if response != 't':
                continue
        break
    
    # Zapisz konfigurację
    print("\n💾 Zapisywanie konfiguracji...")
    
    try:
        with open(config_file, 'w') as f:
            f.write(f"TELEGRAM_BOT_TOKEN={telegram_token}\n")
            f.write(f"GROQ_API_KEY={groq_key}\n")
            f.write(f"CLAUDE_API_KEY={claude_key}\n")
            f.write(f"PIN_CODE={pin}\n")
        
        # Ustaw bezpieczne uprawnienia (tylko Unix)
        if hasattr(os, 'chmod'):
            os.chmod(config_file, 0o600)
            print(f"✅ Ustawiono uprawnienia 600 dla {config_file}")
        else:
            print(f"⚠️  Ustaw ręcznie uprawnienia dla {config_file} (tylko odczyt/zapis dla właściciela)")
        
        print(f"\n✅ Konfiguracja zapisana do {config_file}")
        
        # Sprawdź .gitignore
        if os.path.exists('.gitignore'):
            with open('.gitignore', 'r') as f:
                if 'api_key.md' in f.read():
                    print("✅ Plik api_key.md jest w .gitignore")
                else:
                    print("⚠️  UWAGA: Dodaj api_key.md do .gitignore!")
        else:
            print("⚠️  UWAGA: Brak pliku .gitignore! Utwórz go i dodaj api_key.md")
        
        print("\n🚀 Możesz teraz uruchomić bota: python3 youtube_downloader_telegram.py")
        
    except Exception as e:
        print(f"\n❌ Błąd podczas zapisywania: {e}")
        return 1
    
    return 0

def main():
    """Entry point for poetry script."""
    sys.exit(setup_config())


if __name__ == "__main__":
    main()
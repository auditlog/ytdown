#!/usr/bin/env python3
"""
Simple test for JSON persistence logic (without importing main module).
"""

import os
import json
import shutil
import pytest
from datetime import datetime

pytestmark = pytest.mark.skip(
    reason="Legacy standalone script; consolidated into bot.config tests."
)

# Test implementation of the JSON functions
AUTHORIZED_USERS_FILE = "test_authorized_users.json"

def load_authorized_users():
    """Test version of load_authorized_users function."""
    try:
        if os.path.exists(AUTHORIZED_USERS_FILE):
            with open(AUTHORIZED_USERS_FILE, 'r') as f:
                data = json.load(f)
                return set(int(user_id) for user_id in data.get('authorized_users', []))
        else:
            print(f"Plik {AUTHORIZED_USERS_FILE} nie istnieje. Tworzę nowy.")
            return set()
    except (json.JSONDecodeError, ValueError, IOError) as e:
        print(f"Błąd podczas wczytywania {AUTHORIZED_USERS_FILE}: {e}")
        print("Używam pustej listy autoryzowanych użytkowników.")
        return set()

def save_authorized_users(authorized_users_set):
    """Test version of save_authorized_users function."""
    try:
        data = {
            'authorized_users': [str(user_id) for user_id in authorized_users_set],
            'last_updated': datetime.now().isoformat(),
            'version': '1.0'
        }
        
        temp_file = AUTHORIZED_USERS_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            json.dump(data, f, indent=2)
        
        shutil.move(temp_file, AUTHORIZED_USERS_FILE)
        
        if hasattr(os, 'chmod'):
            os.chmod(AUTHORIZED_USERS_FILE, 0o600)
        
        print(f"Zapisano {len(authorized_users_set)} autoryzowanych użytkowników do {AUTHORIZED_USERS_FILE}")
        return True
        
    except (IOError, OSError) as e:
        print(f"Błąd podczas zapisywania {AUTHORIZED_USERS_FILE}: {e}")
        return False

def test_json_persistence():
    """Test JSON persistence functions."""
    print("🧪 Testowanie JSON persistence...")
    
    try:
        # Clean up any existing test file
        for f in [AUTHORIZED_USERS_FILE, AUTHORIZED_USERS_FILE + '.tmp']:
            if os.path.exists(f):
                os.remove(f)
        
        # Test 1: Loading empty file
        print("\n1. Test ładowania z pustego pliku...")
        users = load_authorized_users()
        assert isinstance(users, set)
        assert len(users) == 0
        print("✅ PASS")
        
        # Test 2: Saving users
        print("\n2. Test zapisywania użytkowników...")
        test_users = {12345, 67890, 111222}
        result = save_authorized_users(test_users)
        assert result == True
        assert os.path.exists(AUTHORIZED_USERS_FILE)
        print("✅ PASS")
        
        # Test 3: Loading saved users
        print("\n3. Test ładowania zapisanych użytkowników...")
        loaded_users = load_authorized_users()
        assert loaded_users == test_users
        print("✅ PASS")
        
        # Test 4: JSON structure
        print("\n4. Test struktury pliku JSON...")
        with open(AUTHORIZED_USERS_FILE, 'r') as f:
            data = json.load(f)
        
        assert 'authorized_users' in data
        assert 'last_updated' in data
        assert 'version' in data
        assert data['version'] == '1.0'
        
        file_users = set(int(uid) for uid in data['authorized_users'])
        assert file_users == test_users
        print("✅ PASS")
        
        # Test 5: File permissions
        if hasattr(os, 'stat'):
            print("\n5. Test uprawnień pliku...")
            file_stats = os.stat(AUTHORIZED_USERS_FILE)
            file_mode = oct(file_stats.st_mode)[-3:]
            print(f"Uprawnienia pliku: {file_mode}")
            if file_mode == '600':
                print("✅ PASS - bezpieczne uprawnienia")
            else:
                print("⚠️  INFO - uprawnienia różne od 600")
        
        # Test 6: Adding/removing users
        print("\n6. Test dodawania/usuwania użytkowników...")
        
        # Add user
        new_users = test_users.copy()
        new_users.add(999888)
        save_authorized_users(new_users)
        
        loaded = load_authorized_users()
        assert 999888 in loaded
        assert len(loaded) == 4
        
        # Remove user
        new_users.discard(999888)
        save_authorized_users(new_users)
        
        loaded = load_authorized_users()
        assert 999888 not in loaded
        assert len(loaded) == 3
        print("✅ PASS")
        
        print(f"\n🎉 Wszystkie testy przeszły pomyślnie!")
        print(f"📁 Plik testowy: {os.path.abspath(AUTHORIZED_USERS_FILE)}")

        # Show file content
        print(f"\n📄 Zawartość pliku:")
        with open(AUTHORIZED_USERS_FILE, 'r') as f:
            print(f.read())

    except Exception as e:
        print(f"\n❌ Test nieudany: {e}")
        import traceback
        traceback.print_exc()
        raise
        
    finally:
        # Cleanup
        for f in [AUTHORIZED_USERS_FILE, AUTHORIZED_USERS_FILE + '.tmp']:
            if os.path.exists(f):
                os.remove(f)

if __name__ == "__main__":
    test_json_persistence()

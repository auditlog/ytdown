#!/usr/bin/env python3
"""
Test JSON persistence functionality for authorized users.
"""

import os
import sys
import json
import tempfile
import shutil

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

def test_json_persistence():
    """Test JSON persistence functions."""

    # Import functions we want to test
    from bot.config import (
        load_authorized_users,
        save_authorized_users,
        AUTHORIZED_USERS_FILE
    )
    from bot.security import manage_authorized_user
    import bot.config as config_module

    print("🧪 Testowanie JSON persistence...")

    # Create temporary file for testing
    original_file = AUTHORIZED_USERS_FILE
    test_file = os.path.join(parent_dir, "test_persistence_users.json")

    # Override the file path for testing
    config_module.AUTHORIZED_USERS_FILE = test_file

    # Reset the runtime authorized users set used by config/runtime helpers
    original_config_users = config_module.authorized_users.copy()
    config_module.authorized_users = set()

    try:
        # Test 1: Loading empty file
        print("\n1. Test ładowania z pustego/nieistniejącego pliku...")
        if os.path.exists(test_file):
            os.remove(test_file)

        users = load_authorized_users()
        assert isinstance(users, set), "load_authorized_users should return a set"
        assert len(users) == 0, "Empty file should return empty set"
        print("✅ Ładowanie pustego pliku działa")

        # Test 2: Saving users
        print("\n2. Test zapisywania użytkowników...")
        test_users = {12345, 67890, 111222}
        save_authorized_users(test_users)

        assert os.path.exists(test_file), "File should be created after save"
        print("✅ Zapisywanie użytkowników działa")

        # Test 3: Loading saved users
        print("\n3. Test ładowania zapisanych użytkowników...")
        loaded_users = load_authorized_users()
        assert loaded_users == test_users, f"Loaded users {loaded_users} should match saved {test_users}"
        print("✅ Ładowanie zapisanych użytkowników działa")

        # Test 4: JSON file structure
        print("\n4. Test struktury pliku JSON...")
        with open(test_file, 'r') as f:
            data = json.load(f)

        assert 'authorized_users' in data, "JSON should contain 'authorized_users' key"
        assert 'last_updated' in data, "JSON should contain 'last_updated' key"
        assert 'version' in data, "JSON should contain 'version' key"

        # Convert back to int set for comparison
        file_users = set(int(uid) for uid in data['authorized_users'])
        assert file_users == test_users, "File content should match test users"
        print("✅ Struktura pliku JSON jest poprawna")

        # Test 5: manage_authorized_user function
        print("\n5. Test funkcji manage_authorized_user...")

        # Set runtime authorized users to match our test state
        config_module.authorized_users = test_users.copy()

        # Add new user
        result = manage_authorized_user(333444, 'add')
        assert result == True, "Adding user should return True"

        updated_users = load_authorized_users()
        assert 333444 in updated_users, "New user should be in the set"
        assert len(updated_users) == 4, f"Should have 4 users now, got {len(updated_users)}"

        # Remove user
        result = manage_authorized_user(333444, 'remove')
        assert result == True, "Removing user should return True"

        updated_users = load_authorized_users()
        assert 333444 not in updated_users, "Removed user should not be in the set"
        assert len(updated_users) == 3, "Should have 3 users again"

        print("✅ Funkcja manage_authorized_user działa")

        # Test 6: File permissions (Unix only)
        if hasattr(os, 'stat'):
            print("\n6. Test uprawnień pliku...")
            file_stats = os.stat(test_file)
            file_mode = oct(file_stats.st_mode)[-3:]
            if file_mode == '600':
                print("✅ Uprawnienia pliku są bezpieczne (600)")
            else:
                print(f"⚠️  Uprawnienia pliku: {file_mode} (oczekiwano 600 - WSL/Windows może mieć inne)")

        print(f"\n🎉 Wszystkie testy przeszły pomyślnie!")

    except Exception as e:
        print(f"\n❌ Test nieudany: {e}")
        import traceback
        traceback.print_exc()
        raise

    finally:
        # Cleanup
        if os.path.exists(test_file):
            os.remove(test_file)
        if os.path.exists(test_file + '.tmp'):
            os.remove(test_file + '.tmp')

        # Restore original state in both modules
        config_module.AUTHORIZED_USERS_FILE = original_file
        config_module.authorized_users = original_config_users

if __name__ == "__main__":
    test_json_persistence()

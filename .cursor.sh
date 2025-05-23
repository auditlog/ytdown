{
  // Konfiguracja Cursor.sh dla projektu YouTube Downloader Bot
  // Plik zawiera ustawienia formatowania, lintingu i innych narzędzi,
  // które pomagają zachować spójny styl kodowania zgodny z PEP 8.
  
  "AI": {
    "active": true,
    "assistant_mode": {
      "enabled": true,
      "default_language": "python"
    }
  },
  
  // Automatyczne formatowanie kodu
  "formatOnSave": true,
  "formatOnType": false,  // Wyłączone, aby nie przeszkadzać podczas szybkiego kodowania
  
  // Konfiguracja lintingu - wybierz jedno z narzędzi (flake8 lub pylint)
  // zgodnie z preferencjami zespołu
  "linting": {
    "enabled": true,
    // Flake8 - bardziej popularne narzędzie, dobrze integruje się z innymi narzędziami
    "flake8": {
      "enabled": true,
      "maxLineLength": 88,  // Zgodne z domyślnymi ustawieniami Black
      "ignore": ["E203", "W503"]  // Kompatybilne z Black
    },
    // Pylint - więcej sprawdzeń, ale może być bardziej restrykcyjne
    "pylint": {
      "enabled": false,  // Domyślnie wyłączone, włącz jeśli preferujesz pylint
      "maxLineLength": 88
    }
  },
  
  // Podstawowe ustawienia edytora
  "editor": {
    "formatOnPaste": true,
    "tabSize": 4,  // Zgodne z PEP 8
    "insertSpaces": true,  // Zgodne z PEP 8
    "indentSize": 4,
    "trimTrailingWhitespace": true,
    "insertFinalNewline": true
  },
  
  // Specyficzne ustawienia dla Pythona
  "python": {
    "analysis": {
      "autoSearchPaths": true,
      "extraPaths": [],
      "diagnosticMode": "workspace",
      "typeCheckingMode": "basic"
    },
    // Black - najpopularniejszy formater dla Pythona
    "formatting": {
      "provider": "black",
      "blackArgs": [
        "--line-length=88"  // Domyślna wartość dla Black
      ]
    },
    // Isort - narzędzie do sortowania importów
    "sortImports": {
      "enabled": true,
      "provider": "isort",
      "isortArgs": [
        "--profile=black",  // Zapewnia kompatybilność z Black
        "--line-length=88"
      ]
    },
    "docstringFormat": "google",  // Format docstringów zgodny z Google Style Guide
    "env": {
      "venvPath": "",
      "defaultInterpreterPath": "python"
    }
  },
  
  // Wykluczenie plików i katalogów, które nie powinny być śledzone
  "files": {
    "exclude": [
      "**/__pycache__",
      "**/.pytest_cache",
      "**/*.pyc",
      "**/.git",
      "**/downloads",
      "**/temp_parts",
      "**/.venv",
      "**/env",
      "**/venv",
      "**/*.log"
    ],
    "watcherExclude": [
      "**/__pycache__/**",
      "**/.pytest_cache/**",
      "**/*.pyc",
      "**/.git/**",
      "**/downloads/**",
      "**/temp_parts/**"
    ]
  },
  
  // Wykluczenia dla wyszukiwania
  "search": {
    "exclude": {
      "**/downloads": true,
      "**/temp_parts": true,
      "**/.venv": true,
      "**/venv": true,
      "**/env": true,
      "**/__pycache__": true
    }
  },
  
  // Ustawienia terminala
  "terminal": {
    "integrated": {
      "shell": {
        "linux": "/bin/bash",
        "windows": "cmd.exe",
        "osx": "/bin/zsh"
      }
    }
  },
  
  // Dodatkowe ustawienia
  "gitCommitSign": {
    "enabled": false
  }
}
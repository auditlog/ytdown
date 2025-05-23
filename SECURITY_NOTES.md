# Uwagi bezpieczeństwa

## Ważne pliki do zabezpieczenia

### 1. api_key.md
Ten plik zawiera wszystkie klucze API i PIN. **NIGDY** nie udostępniaj tego pliku!

**Zalecane działania:**
- ✅ Plik został dodany do `.gitignore`
- ⚠️ W systemie Windows ustaw ręcznie uprawnienia tylko dla swojego użytkownika
- 💡 Rozważ użycie zmiennych środowiskowych zamiast pliku

### 2. Alternatywa - zmienne środowiskowe
Zamiast przechowywać klucze w pliku, możesz użyć zmiennych środowiskowych:

```bash
# Linux/Mac/WSL
export TELEGRAM_BOT_TOKEN="twój_token"
export GROQ_API_KEY="twój_klucz"
export CLAUDE_API_KEY="twój_klucz"
export PIN_CODE="12345678"

# Windows (Command Prompt)
set TELEGRAM_BOT_TOKEN=twój_token
set GROQ_API_KEY=twój_klucz
set CLAUDE_API_KEY=twój_klucz
set PIN_CODE=12345678

# Windows (PowerShell)
$env:TELEGRAM_BOT_TOKEN="twój_token"
$env:GROQ_API_KEY="twój_klucz"
$env:CLAUDE_API_KEY="twój_klucz"
$env:PIN_CODE="12345678"
```

### 3. Zabezpieczenia które zostały dodane

✅ **Rate Limiting**
- Max 10 requestów na minutę per użytkownik
- Automatyczne odrzucanie przy przekroczeniu limitu

✅ **Limit rozmiaru plików**
- Max 500MB na plik
- Sprawdzanie przed rozpoczęciem pobierania

✅ **Walidacja URL**
- Tylko dozwolone domeny YouTube
- Odrzucanie podejrzanych linków

✅ **Timeout połączeń**
- 30 sekund timeout dla operacji sieciowych
- Automatyczne retry (3 próby)

### 4. Dodatkowe zalecenia

1. **Regularnie zmieniaj PIN** - szczególnie jeśli udostępniasz bota innym
2. **Monitoruj logi** - sprawdzaj czy nie ma podejrzanych prób dostępu
3. **Backup kluczy** - przechowuj kopię `api_key.md` w bezpiecznym miejscu (poza repo!)
4. **Ogranicz dostęp** - uruchamiaj bota tylko gdy potrzebujesz
5. **Sprawdzaj miejsce na dysku** - regularnie czyść folder downloads/

### 5. W przypadku wycieku

Jeśli przypadkowo udostępnisz klucze:
1. **Natychmiast** zmień wszystkie klucze API
2. Dla Telegram Bot - użyj @BotFather aby wygenerować nowy token
3. Sprawdź logi czy ktoś nie używał Twoich kluczy
4. Zmień PIN w bocie
import sys
import os
from datetime import datetime
import logging
import argparse
import curses
import time
import re
import glob
import math
import requests
import subprocess
import json
import tempfile
import shutil
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters

try:
    import yt_dlp
    from mutagen.mp3 import MP3
except ImportError:
    print("Błąd: Brak wymaganych pakietów. Zainstaluj je poleceniem: pip install yt-dlp mutagen")
    sys.exit(1)

# Konfiguracja logowania
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Ścieżka do pliku z kluczami API
CONFIG_FILE_PATH = "api_key.md"

# Domyślne wartości konfiguracyjne (używane tylko gdy nie można odczytać pliku konfiguracyjnego)
DEFAULT_CONFIG = {
    "TELEGRAM_BOT_TOKEN": "",  # Zostawiamy puste, żeby wymusić błąd jeśli nie ma pliku konfiguracyjnego
    "GROQ_API_KEY": "",
    "PIN_CODE": "12345678",  # Domyślny PIN (8 cyfr)
    "CLAUDE_API_KEY": ""
}

# Katalog na pobrane pliki
DOWNLOAD_PATH = "./downloads"

# Utwórz katalog na pobrane pliki jeśli nie istnieje
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# Słownik do przechowywania liczby nieudanych prób dla każdego użytkownika
failed_attempts = defaultdict(int)

# Słownik do przechowywania czasu blokady dla każdego użytkownika
block_until = defaultdict(float)

# Maksymalna liczba prób przed zablokowaniem
MAX_ATTEMPTS = 3

# Czas blokady w sekundach (15 minut)
BLOCK_TIME = 15 * 60

# Słownik do przechowywania stanu autoryzacji użytkowników
authorized_users = set()

# Maksymalny rozmiar części MP3 w MB do transkrypcji
MAX_MP3_PART_SIZE_MB = 25

def load_config():
    """
    Wczytuje konfigurację z pliku api_key.md.
    Plik powinien zawierać linie w formacie KLUCZ=WARTOŚĆ.
    
    Zwraca słownik z konfiguracją.
    """
    config = DEFAULT_CONFIG.copy()
    
    try:
        if os.path.exists(CONFIG_FILE_PATH):
            with open(CONFIG_FILE_PATH, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and '=' in line:
                        key, value = line.split('=', 1)
                        config[key] = value
            logging.info("Wczytano konfigurację z pliku")
        else:
            logging.warning(f"Plik konfiguracyjny {CONFIG_FILE_PATH} nie istnieje. Używam domyślnych wartości.")
    except Exception as e:
        logging.error(f"Błąd podczas wczytywania konfiguracji: {e}")
    
    return config

# Wczytaj konfigurację
CONFIG = load_config()

# Ustaw stałe z konfiguracji
BOT_TOKEN = CONFIG["TELEGRAM_BOT_TOKEN"]
PIN_CODE = CONFIG["PIN_CODE"]

# Funkcje do obsługi transkrypcji
def get_api_key():
    """Odczytuje klucz API do Groq z konfiguracji."""
    return CONFIG["GROQ_API_KEY"]

def get_claude_api_key():
    """Odczytuje klucz API do Claude z konfiguracji."""
    return CONFIG["CLAUDE_API_KEY"]

def find_silence_points(file_path, num_parts, min_duration=0.5):
    """
    Znajduje punkty ciszy w pliku MP3 używając filtru ffmpeg silencedetect.
    Zwraca listę znaczników czasu (w sekundach) gdzie wykryto ciszę.
    """
    silence_points = []
    
    try:
        # Uruchom ffmpeg z filtrem silencedetect
        cmd = [
            "ffmpeg", "-i", file_path, 
            "-af", f"silencedetect=noise=-30dB:d={min_duration}", 
            "-f", "null", "-"
        ]
        
        result = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)
        output = result.stderr
        
        # Przetwórz wyjście, aby znaleźć punkty ciszy
        for line in output.splitlines():
            if "silence_end" in line:
                parts = line.split()
                for i, part in enumerate(parts):
                    if part == "silence_end:":
                        timestamp = float(parts[i+1])
                        silence_points.append(timestamp)
        
        # Posortuj punkty
        silence_points.sort()
        
    except (subprocess.SubprocessError, ValueError, IndexError) as e:
        logging.error(f"Błąd podczas wyszukiwania punktów ciszy: {e}")
    
    return silence_points

def split_mp3(file_path, output_dir, max_size_mb=MAX_MP3_PART_SIZE_MB):
    """
    Dzieli plik MP3 na wiele części, każda nie przekraczająca max_size_mb.
    Próbuje dzielić w punktach ciszy w audio, gdy to możliwe.
    """
    # Pobierz rozmiar pliku w MB
    file_size = os.path.getsize(file_path) / (1024 * 1024)
    
    # Jeśli plik jest już mniejszy niż max_size_mb, nie trzeba dzielić
    if file_size <= max_size_mb:
        logging.info(f"{file_path} jest już mniejszy niż {max_size_mb}MB. Nie jest wymagane dzielenie.")
        # Kopiujemy plik do katalogu wyjściowego
        output_path = os.path.join(output_dir, os.path.basename(file_path))
        shutil.copy(file_path, output_path)
        return [output_path]
    
    # Oblicz liczbę potrzebnych części
    num_parts = math.ceil(file_size / max_size_mb)
    logging.info(f"Rozmiar pliku: {file_size:.2f}MB. Dzielenie na {num_parts} części...")
    
    # Pobierz czas trwania MP3 przy użyciu mutagen
    try:
        audio = MP3(file_path)
        total_duration = audio.info.length  # Czas trwania w sekundach
    except Exception as e:
        logging.error(f"Błąd podczas pobierania czasu trwania z mutagen: {e}")
        # Alternatywna metoda: oszacuj czas trwania na podstawie rozmiaru pliku
        total_duration = (file_size * 8 * 1024) / 128  # Zakładając 128 kbps
        logging.info(f"Używanie szacowanego czasu trwania: {total_duration:.2f} sekund")
    
    # Idealny czas trwania części przy równomiernym podziale
    ideal_part_duration = total_duration / num_parts
    
    # Próbuj znaleźć punkty ciszy
    silence_points = []
    try:
        logging.info("Analizowanie audio w poszukiwaniu optymalnych punktów podziału...")
        silence_points = find_silence_points(file_path, num_parts)
    except Exception as e:
        logging.error(f"Błąd podczas wyszukiwania punktów ciszy: {e}")
    
    # Wybierz dobre punkty podziału na podstawie punktów ciszy
    split_points = []
    
    if silence_points:
        # Najpierw uzyskaj idealne znaczniki czasu podziału
        ideal_splits = [ideal_part_duration * i for i in range(1, num_parts)]
        
        # Dla każdego idealnego podziału, znajdź najbliższy punkt ciszy
        for ideal_time in ideal_splits:
            # Znajdź najbliższy punkt ciszy (minimalna odległość)
            closest = min(silence_points, key=lambda x: abs(x - ideal_time))
            
            # Używaj tylko, jeśli jest w granicach 20% idealnego czasu
            if abs(closest - ideal_time) < (ideal_part_duration * 0.2):
                split_points.append(closest)
            else:
                split_points.append(ideal_time)
    else:
        # Nie znaleziono punktów ciszy, użyj równomiernych podziałów
        split_points = [ideal_part_duration * i for i in range(1, num_parts)]
    
    # Dodaj punkty początkowe i końcowe
    all_points = [0] + split_points + [total_duration]
    
    # Pobierz podstawową nazwę pliku bez rozszerzenia
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    
    # Lista ścieżek do utworzonych plików
    output_files = []
    
    # Podziel plik
    for i in range(len(all_points) - 1):
        start_time = all_points[i]
        end_time = all_points[i+1]
        duration = end_time - start_time
        
        # Utwórz nazwę pliku wyjściowego
        output_path = os.path.join(output_dir, f"{base_name}_part{i+1}.mp3")
        output_files.append(output_path)
        
        try:
            # Uruchom ffmpeg, aby wyodrębnić segment
            # Używanie -acodec copy, aby uniknąć ponownego kodowania
            cmd = [
                "ffmpeg", "-y", "-i", file_path, 
                "-ss", str(start_time), "-t", str(duration),
                "-acodec", "copy", output_path
            ]
            
            # Uruchom polecenie, przekieruj wyjście
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Sprawdź rozmiar wyeksportowanego pliku
            part_size = os.path.getsize(output_path) / (1024 * 1024)
            logging.info(f"Utworzono {output_path} ({part_size:.2f}MB, {duration:.2f} sekund)")
            
        except subprocess.SubprocessError as e:
            logging.error(f"Błąd podczas tworzenia części {i+1}: {e}")
    
    return output_files

def transcribe_audio(file_path, api_key):
    """Transkrybuje plik audio używając API Groq."""
    
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    
    try:
        with open(file_path, "rb") as audio_file:
            files = {
                "file": (os.path.basename(file_path), audio_file.read(), "audio/mpeg")
            }
            data = {
                "model": "whisper-large-v3",
                "response_format": "text"
            }
            
            response = requests.post(url, headers=headers, files=files, data=data)
            
            if response.status_code == 200:
                return response.text
            else:
                logging.error(f"Błąd: {response.status_code}")
                logging.error(response.text)
                return ""
    except Exception as e:
        logging.error(f"Błąd podczas transkrypcji: {e}")
        return ""

def get_part_number(filename):
    """Wyodrębnia numer części z nazwy pliku."""
    match = re.search(r'part(\d+)', filename)
    if match:
        return int(match.group(1))
    return 0

def transcribe_mp3_file(file_path, output_dir):
    """
    Transkrybuje plik MP3, dzieląc go na mniejsze części, jeśli to konieczne.
    Zwraca ścieżkę do pliku z transkrypcją.
    """
    api_key = get_api_key()
    if not api_key:
        logging.error("Nie można odczytać klucza API z pliku api_key.md.")
        return None
    
    # Utwórz tymczasowy katalog na podzielone pliki
    temp_dir = os.path.join(output_dir, "temp_parts")
    os.makedirs(temp_dir, exist_ok=True)
    
    # Podziel plik MP3 na mniejsze części
    part_files = split_mp3(file_path, temp_dir)
    
    # Sortuj pliki według numeru części
    part_files.sort(key=lambda x: get_part_number(os.path.basename(x)))
    
    # Transkrybuj każdy plik i przechowuj wyniki
    transcriptions = []
    
    logging.info(f"Znaleziono {len(part_files)} plików części do transkrypcji.")
    
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    
    for i, part_path in enumerate(part_files):
        logging.info(f"Transkrybowanie pliku {i+1}/{len(part_files)}: {part_path}")
        transcription = transcribe_audio(part_path, api_key)
        transcriptions.append(transcription)
        
        # Zapisz pojedynczą transkrypcję jako kopię zapasową
        part_num = get_part_number(os.path.basename(part_path)) or (i + 1)
        transcript_path = os.path.join(output_dir, f"{base_name}_part{part_num}_transcript.txt")
        
        with open(transcript_path, "w", encoding="utf-8") as f:
            f.write(transcription)
            
        logging.info(f"Zapisano transkrypcję dla części {part_num}")
    
    # Połącz wszystkie transkrypcje
    combined_text = "\n\n".join(transcriptions)
    
    # Zapisz połączoną transkrypcję jako markdown
    transcript_md_path = os.path.join(output_dir, f"{base_name}_transcript.md")
    with open(transcript_md_path, "w", encoding="utf-8") as f:
        f.write(f"# {base_name} Transcript\n\n")
        f.write(combined_text)
    
    logging.info(f"Wszystkie transkrypcje połączone i zapisane do {transcript_md_path}")
    
    # Usuń tymczasowy katalog z częściami plików
    try:
        shutil.rmtree(temp_dir)
    except Exception as e:
        logging.error(f"Błąd podczas usuwania tymczasowego katalogu: {e}")
    
    return transcript_md_path

def sanitize_filename(filename):
    """Usuwa nieprawidłowe znaki z nazwy pliku."""
    invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    for char in invalid_chars:
        filename = filename.replace(char, '-')
    return filename

def progress_hook(d):
    """Funkcja wywoływana przez yt-dlp do śledzenia postępu pobierania."""
    if d['status'] == 'downloading':
        # Sprawdź czy mamy informacje o całkowitym rozmiarze
        if d.get('total_bytes'):
            percent = round(float(d['downloaded_bytes'] / d['total_bytes'] * 100), 1)
            print(f"\rPobieranie: {percent}% [{d['downloaded_bytes']/1024/1024:.1f}MB / {d['total_bytes']/1024/1024:.1f}MB]", end='')
        elif d.get('total_bytes_estimate'):
            percent = round(float(d['downloaded_bytes'] / d['total_bytes_estimate'] * 100), 1)
            print(f"\rPobieranie: {percent}% [{d['downloaded_bytes']/1024/1024:.1f}MB / szacowane {d['total_bytes_estimate']/1024/1024:.1f}MB]", end='')
        else:
            # Jeśli nie ma informacji o całkowitym rozmiarze, wyświetl tylko pobrane dane
            print(f"\rPobieranie: [{d['downloaded_bytes']/1024/1024:.1f}MB pobrane]", end='')
    elif d['status'] == 'finished':
        print("\nPobieranie zakończone, trwa przetwarzanie...")
    elif d['status'] == 'error':
        print(f"\nBłąd podczas pobierania: {d.get('error')}")

def get_basic_ydl_opts():
    """Zwraca podstawową konfigurację dla yt-dlp."""
    return {
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [progress_hook],
    }

def get_video_info(url):
    """Pobiera informacje o filmie, bez wyświetlania formatów."""
    try:
        ydl_opts = get_basic_ydl_opts()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    except Exception as e:
        print(f"Wystąpił błąd podczas pobierania informacji o filmie: {str(e)}")
        return None

def download_youtube_video(url, format_id=None, audio_only=False, audio_format='mp3', audio_quality='192'):
    logging.debug(f"Rozpoczęcie pobierania dla URL: {url}, format: {format_id}...")
    try:
        current_date = datetime.now().strftime("%Y-%m-%d")
        
        # Konfiguracja yt-dlp
        ydl_opts = {
            'outtmpl': f'{current_date} %(title)s.%(ext)s',
            'progress_hooks': [progress_hook],
            'quiet': True,  # Wyciszamy wbudowane powiadomienia o postępie
            'no_warnings': False,
            'ignoreerrors': True,
        }
        
        # Konfiguracja dla pobierania tylko audio
        if audio_only:
            print(f"[DEBUG] Konfiguracja dla pobierania tylko audio ({audio_format})")
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': audio_format,
                    'preferredquality': audio_quality,
                }],
            })
        # Konfiguracja dla standardowego pobierania wideo
        elif format_id:
            ydl_opts['format'] = format_id
            print(f"[DEBUG] Ustawiono format: {format_id}")
        else:
            print("[DEBUG] Używanie domyślnego formatu (najlepsza jakość)")
        
        # Pobierz film lub audio
        print("[DEBUG] Inicjalizacja YoutubeDL...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print("[DEBUG] Rozpoczęcie pobierania...")
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Nieznany tytuł')
            print(f"[DEBUG] Informacje o pobranym pliku: Tytuł={title}")
        
        print(f"\nPobieranie zakończone pomyślnie")
        return True
        
    except Exception as e:
        print(f"[DEBUG] Wystąpił błąd podczas pobierania: {str(e)}")
        print(f"Wystąpił błąd: {str(e)}")
        return False

def show_help():
    """Wyświetla informacje pomocy dla skryptu."""
    print("YouTube Downloader - narzędzie do pobierania filmów z YouTube")
    print("\nSposób użycia:")
    print("  python youtube_downloader2.py [opcje]")
    print("\nOpcje:")
    print("  --help                  Wyświetla tę informację pomocy")
    print("  --cli                   Uruchamia w trybie wiersza poleceń (bez menu interaktywnego)")
    print("  --url <URL>             URL do filmu na YouTube")
    print("  --list-formats          Wyświetla tylko dostępne formaty bez pobierania")
    print("  --format <ID>           Określa format do pobrania (ID formatu z listy)")
    print("  --format auto           Automatycznie wybiera najlepszą jakość")
    print("  --audio-only            Pobiera tylko ścieżkę dźwiękową (domyślnie mp3)")
    print("  --audio-format <FORMAT> Określa format audio (mp3, m4a, wav, flac)")
    print("  --audio-quality <JAKOŚĆ> Określa jakość audio (0-9 dla vorbis/opus, 0-330 dla mp3)")
    print("\nPrzykłady:")
    print("  python youtube_downloader2.py                                                 # uruchamia menu interaktywne")
    print("  python youtube_downloader2.py --cli --url https://www.youtube.com/watch?v=dQw4w9WgXcQ --audio-only")
    print("\nOpis:")
    print("  Program wyświetla dostępne formaty wideo, pozwala wybrać konkretny format")
    print("  i wyświetla postęp pobierania w czasie rzeczywistym. Można również pobrać")
    print("  tylko ścieżkę dźwiękową w różnych formatach (mp3, m4a, wav, flac).")

def validate_url(url):
    """Sprawdza, czy podany URL jest prawidłowym linkiem do YouTube."""
    if not url.startswith(('https://www.youtube.com/', 'https://youtu.be/')):
        print("Błąd: Nieprawidłowy URL. Podaj link do filmu na YouTube.")
        return False
    return True

def parse_arguments():
    """Parsuje argumenty linii poleceń używając argparse."""
    parser = argparse.ArgumentParser(description="YouTube Downloader - narzędzie do pobierania filmów z YouTube")
    parser.add_argument("--cli", action="store_true", help="Uruchamia w trybie wiersza poleceń (bez menu interaktywnego)")
    parser.add_argument("--url", help="URL do filmu na YouTube")
    parser.add_argument("--list-formats", action="store_true", help="Wyświetla tylko dostępne formaty bez pobierania")
    parser.add_argument("--format", help="Określa format do pobrania (ID formatu z listy)")
    parser.add_argument("--audio-only", action="store_true", help="Pobiera tylko ścieżkę dźwiękową")
    parser.add_argument("--audio-format", default="mp3", help="Określa format audio (mp3, m4a, wav, flac)")
    parser.add_argument("--audio-quality", default="192", help="Określa jakość audio")
    
    return parser.parse_args()

def curses_main(stdscr):
    """Główna funkcja menu interaktywnego używająca curses."""
    # Konfiguracja terminala
    curses.curs_set(0)  # Ukrywa kursor
    stdscr.clear()
    stdscr.refresh()
    
    # Definicja kolorów
    curses.start_color()
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)  # Normalny tekst
    curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)  # Podświetlenie
    curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)  # Nagłówki
    
    # Pierwsze menu - prośba o podanie URL
    stdscr.addstr(0, 0, "YouTube Downloader", curses.color_pair(3) | curses.A_BOLD)
    stdscr.addstr(2, 0, "Podaj URL filmu z YouTube:", curses.color_pair(1))
    stdscr.addstr(3, 0, "> ", curses.color_pair(1))
    stdscr.refresh()
    
    # Włączenie widoczności kursora
    curses.curs_set(1)
    
    # Pobieranie URL od użytkownika
    curses.echo()
    url = stdscr.getstr(3, 2, 100).decode('utf-8')
    curses.noecho()
    curses.curs_set(0)
    
    # Sprawdzenie poprawności URL
    if not validate_url(url):
        stdscr.addstr(5, 0, "Błąd: Nieprawidłowy URL. Podaj link do filmu na YouTube.", curses.color_pair(1))
        stdscr.addstr(7, 0, "Naciśnij dowolny klawisz, aby zakończyć...", curses.color_pair(1))
        stdscr.refresh()
        stdscr.getch()
        return
    
    # Pobieranie informacji o filmie
    stdscr.clear()
    stdscr.addstr(0, 0, "Pobieranie informacji o filmie...", curses.color_pair(1))
    stdscr.refresh()
    
    video_info = get_video_info(url)
    if not video_info:
        stdscr.addstr(2, 0, "Wystąpił błąd podczas pobierania informacji o filmie.", curses.color_pair(1))
        stdscr.addstr(4, 0, "Naciśnij dowolny klawisz, aby zakończyć...", curses.color_pair(1))
        stdscr.refresh()
        stdscr.getch()
        return
    
    # Przygotowanie menu wyboru formatu
    stdscr.clear()
    title = video_info.get('title', 'Nieznany tytuł')
    stdscr.addstr(0, 0, f"Film: {title[:50]}{'...' if len(title) > 50 else ''}", curses.color_pair(3) | curses.A_BOLD)
    stdscr.addstr(2, 0, "Dostępne formaty video:", curses.color_pair(3))
    
    # Pobieranie formatów
    video_formats = []
    audio_formats = []
    
    for format in video_info.get('formats', []):
        format_id = format.get('format_id', 'N/A')
        ext = format.get('ext', 'N/A')
        resolution = format.get('resolution', 'N/A')
        filesize = f"{format.get('filesize', 0)/1024/1024:.1f}MB" if format.get('filesize') else 'N/A'
        notes = format.get('format_note', '')
        
        # Podział na formaty audio i video
        if format.get('vcodec') == 'none':
            audio_formats.append({
                'id': format_id,
                'desc': f"{format_id}: {ext}, {resolution}, {filesize}, {notes}"
            })
        else:
            video_formats.append({
                'id': format_id,
                'desc': f"{format_id}: {ext}, {resolution}, {filesize}, {notes}"
            })
    
    # Dodanie opcji konwersji audio
    audio_conversion_formats = [
        {'id': 'mp3_convert', 'desc': "Konwersja do MP3 (domyślny format)"},
        {'id': 'm4a_convert', 'desc': "Konwersja do M4A (format AAC)"},
        {'id': 'wav_convert', 'desc': "Konwersja do WAV"},
        {'id': 'flac_convert', 'desc': "Konwersja do FLAC (bezstratny)"},
        {'id': 'opus_convert', 'desc': "Konwersja do Opus"},
        {'id': 'vorbis_convert', 'desc': "Konwersja do Vorbis"}
    ]
    
    # Wszystkie opcje w jednej liście
    all_options = []
    all_options.append({'id': 'best', 'desc': "Najlepsza dostępna jakość (automatyczny wybór)"})
    all_options.extend(video_formats)
    all_options.append({'id': 'separator1', 'desc': "----- Dostępne formaty audio -----"})
    all_options.extend(audio_formats)
    all_options.append({'id': 'separator2', 'desc': "----- Konwersja do formatów audio -----"})
    all_options.extend(audio_conversion_formats)
    
    # Wyświetlanie menu
    current_pos = 0
    page_size = curses.LINES - 6  # Liczba opcji wyświetlanych na stronie
    offset = 0
    
    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, f"Film: {title[:50]}{'...' if len(title) > 50 else ''}", curses.color_pair(3) | curses.A_BOLD)
        stdscr.addstr(2, 0, "Wybierz format do pobrania (użyj strzałek i Enter):", curses.color_pair(1))
        
        # Wyświetlanie opcji z paginacją
        for i in range(min(page_size, len(all_options) - offset)):
            idx = i + offset
            option = all_options[idx]
            
            # Separator - tylko wyświetlanie, bez możliwości wyboru
            if option['id'].startswith('separator'):
                stdscr.addstr(i + 4, 0, option['desc'], curses.color_pair(3))
                continue
                
            # Podświetlenie aktualnie wybranej opcji
            if idx == current_pos:
                stdscr.addstr(i + 4, 0, option['desc'], curses.color_pair(2))
            else:
                stdscr.addstr(i + 4, 0, option['desc'], curses.color_pair(1))
        
        # Informacja o nawigacji
        footer_y = min(page_size, len(all_options) - offset) + 5
        stdscr.addstr(footer_y, 0, "↑/↓: Nawigacja  Enter: Wybór  q: Wyjście", curses.color_pair(1))
        stdscr.addstr(footer_y + 1, 0, f"Strona {offset // page_size + 1}/{(len(all_options) - 1) // page_size + 1}", curses.color_pair(1))
        
        stdscr.refresh()
        
        # Obsługa klawiszy
        key = stdscr.getch()
        
        if key == curses.KEY_UP:
            # Pominięcie separatorów przy nawigacji
            current_pos -= 1
            while current_pos >= 0 and all_options[current_pos]['id'].startswith('separator'):
                current_pos -= 1
            
            if current_pos < 0:
                current_pos = len(all_options) - 1
                # Znów pominięcie separatora, jeśli ostatni element to separator
                while current_pos >= 0 and all_options[current_pos]['id'].startswith('separator'):
                    current_pos -= 1
            
            # Dostosowanie offsetu jeśli wyjdziemy poza widoczny obszar
            if current_pos < offset:
                offset = (current_pos // page_size) * page_size
        
        elif key == curses.KEY_DOWN:
            # Pominięcie separatorów przy nawigacji
            current_pos += 1
            while current_pos < len(all_options) and all_options[current_pos]['id'].startswith('separator'):
                current_pos += 1
            
            if current_pos >= len(all_options):
                current_pos = 0
                # Znów pominięcie separatora, jeśli pierwszy element to separator
                while current_pos < len(all_options) and all_options[current_pos]['id'].startswith('separator'):
                    current_pos += 1
            
            # Dostosowanie offsetu jeśli wyjdziemy poza widoczny obszar
            if current_pos >= offset + page_size:
                offset = (current_pos // page_size) * page_size
        
        elif key == curses.KEY_NPAGE:  # Page Down
            offset += page_size
            if offset >= len(all_options):
                offset = 0
            # Dostosowanie obecnej pozycji
            current_pos = offset
            # Pominięcie separatora
            while current_pos < len(all_options) and all_options[current_pos]['id'].startswith('separator'):
                current_pos += 1
        
        elif key == curses.KEY_PPAGE:  # Page Up
            offset -= page_size
            if offset < 0:
                offset = max(0, ((len(all_options) - 1) // page_size) * page_size)
            # Dostosowanie obecnej pozycji
            current_pos = offset
            # Pominięcie separatora
            while current_pos < len(all_options) and all_options[current_pos]['id'].startswith('separator'):
                current_pos += 1
        
        elif key == ord('\n'):  # Enter
            # Wybrano opcję, przejdź do pobierania
            selected = all_options[current_pos]
            break
        
        elif key == ord('q') or key == ord('Q'):
            # Wyjście
            return
    
    # Przetwarzanie wybranej opcji
    stdscr.clear()
    stdscr.addstr(0, 0, f"Film: {title}", curses.color_pair(3) | curses.A_BOLD)
    stdscr.addstr(2, 0, f"Wybrano: {selected['desc']}", curses.color_pair(1))
    stdscr.addstr(4, 0, "Rozpoczynanie pobierania...", curses.color_pair(1))
    stdscr.refresh()
    
    # Zamknięcie trybu curses, aby wyświetlać postęp pobierania w standardowy sposób
    curses.endwin()
    
    # Analizowanie wybranej opcji i rozpoczęcie pobierania
    if selected['id'] == 'best':
        # Najlepsza jakość automatycznie
        print(f"Pobieranie najlepszej jakości dla: {title}")
        download_youtube_video(url)
    elif selected['id'].endswith('_convert'):
        # Konwersja do formatu audio
        audio_format = selected['id'].split('_')[0]
        print(f"Pobieranie i konwersja do formatu {audio_format} dla: {title}")
        download_youtube_video(url, None, True, audio_format, '192')
    else:
        # Konkretny format
        print(f"Pobieranie formatu {selected['id']} dla: {title}")
        download_youtube_video(url, selected['id'])
    
    print("\nPobieranie zakończone.")
    input("Naciśnij Enter, aby zakończyć...")

def cli_mode(args):
    """Tryb wiersza poleceń."""
    # Sprawdź czy URL został podany
    if not args.url:
        show_help()
        return
        
    # Sprawdź poprawność URL
    if not validate_url(args.url):
        return
    
    # Logika pobierania
    if args.list_formats:
        # Pobieranie i wyświetlanie formatów
        info = get_video_info(args.url)
        if info:
            title = info.get('title', 'Nieznany tytuł')
            print(f"Tytuł: {title}")
            print("\nDostępne formaty:")
            print("-" * 80)
            print(f"{'ID':<5} {'Rozszerzenie':<10} {'Rozdzielczość':<15} {'Rozmiar':<10} {'Tylko audio':<10} {'Uwagi':<20}")
            print("-" * 80)
            
            for format in info.get('formats', []):
                format_id = format.get('format_id', 'N/A')
                ext = format.get('ext', 'N/A')
                resolution = format.get('resolution', 'N/A')
                filesize = f"{format.get('filesize', 0)/1024/1024:.1f}MB" if format.get('filesize') else 'N/A'
                audio_only = "Tak" if format.get('vcodec') == 'none' else "Nie"
                notes = format.get('format_note', '')
                
                print(f"{format_id:<5} {ext:<10} {resolution:<15} {filesize:<10} {audio_only:<10} {notes:<20}")
            
            print("\nDostępne formaty audio do konwersji:")
            print("-" * 40)
            print("mp3    - format MP3 (domyślny)")
            print("m4a    - format AAC")
            print("wav    - format WAV")
            print("flac   - format FLAC (bezstratny)")
            print("opus   - format Opus")
            print("vorbis - format Vorbis")
    else:
        # Pobieranie wybranego formatu
        download_youtube_video(
            args.url, 
            args.format, 
            args.audio_only, 
            args.audio_format, 
            args.audio_quality
        )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Obsługuje komendę /start."""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    # Sprawdź, czy użytkownik jest zablokowany
    if time.time() < block_until[user_id]:
        remaining_time = int(block_until[user_id] - time.time())
        minutes = remaining_time // 60
        seconds = remaining_time % 60
        
        await update.message.reply_text(
            f"Witaj, {user_name}!\n\n"
            f"Dostęp zablokowany z powodu zbyt wielu nieudanych prób. "
            f"Spróbuj ponownie za {minutes} min {seconds} s."
        )
        return
    
    # Sprawdź, czy użytkownik jest już autoryzowany
    if user_id in authorized_users:
        await update.message.reply_text(
            f"Witaj, {user_name}!\n\n"
            "Jesteś już zalogowany. Możesz wysłać link do YouTube, aby pobrać film lub audio."
        )
        return
    
    # Jeśli użytkownik nie jest autoryzowany, poproś o PIN
    await update.message.reply_text(
        f"Witaj, {user_name}!\n\n"
        "🔒 To jest bot chroniony PIN-em.\n"
        "Aby korzystać z bota, podaj 8-cyfrowy kod PIN."
    )
    
    # Ustaw stan oczekiwania na PIN
    context.user_data["awaiting_pin"] = True

async def handle_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Obsługuje wprowadzanie kodu PIN przez użytkownika."""
    user_id = update.effective_user.id
    message_text = update.message.text
    
    # Sprawdź, czy użytkownik jest zablokowany
    if time.time() < block_until[user_id]:
        remaining_time = int(block_until[user_id] - time.time())
        minutes = remaining_time // 60
        seconds = remaining_time % 60
        
        await update.message.reply_text(
            f"Dostęp zablokowany z powodu zbyt wielu nieudanych prób. "
            f"Spróbuj ponownie za {minutes} min {seconds} s."
        )
        
        # Ze względów bezpieczeństwa usuń wiadomość zawierającą potencjalny PIN
        try:
            await update.message.delete()
        except Exception:
            pass
        
        return True  # Informacja, że wiadomość została obsłużona
    
    # Sprawdź, czy oczekujemy na PIN od tego użytkownika
    if context.user_data.get("awaiting_pin", False) or not (user_id in authorized_users):
        # Sprawdź, czy wiadomość wygląda jak PIN (8 cyfr)
        if message_text.isdigit() and len(message_text) == 8:
            # Sprawdź, czy PIN jest poprawny
            if message_text == PIN_CODE:
                # Resetuj licznik nieudanych prób
                failed_attempts[user_id] = 0
                
                # Dodaj użytkownika do listy autoryzowanych
                authorized_users.add(user_id)
                
                # Usuń stan oczekiwania na PIN
                context.user_data.pop("awaiting_pin", None)
                
                # Wyślij potwierdzenie
                await update.message.reply_text(
                    "✅ PIN poprawny! Możesz teraz korzystać z bota.\n\n"
                    "Wyślij link do YouTube, aby pobrać film lub audio."
                )
                
                # Sprawdź, czy jest oczekujący URL do przetworzenia
                pending_url = context.user_data.get("pending_url")
                if pending_url:
                    # Usuń oczekujący URL
                    context.user_data.pop("pending_url", None)
                    # Przetwórz URL
                    await process_youtube_link(update, context, pending_url)
            else:
                # Zwiększ licznik nieudanych prób
                failed_attempts[user_id] += 1
                
                # Sprawdź, czy użytkownik przekroczył limit prób
                if failed_attempts[user_id] >= MAX_ATTEMPTS:
                    # Zablokuj użytkownika na określony czas
                    block_until[user_id] = time.time() + BLOCK_TIME
                    
                    # Informuj o blokadzie
                    await update.message.reply_text(
                        "❌ Niepoprawny PIN!\n\n"
                        f"Przekroczono maksymalną liczbę prób ({MAX_ATTEMPTS}).\n"
                        f"Dostęp zablokowany na {BLOCK_TIME // 60} minut."
                    )
                else:
                    # Informuj o pozostałych próbach
                    remaining_attempts = MAX_ATTEMPTS - failed_attempts[user_id]
                    await update.message.reply_text(
                        "❌ Niepoprawny PIN!\n\n"
                        f"Pozostało prób: {remaining_attempts}"
                    )
            
            # Ze względów bezpieczeństwa usuń wiadomość zawierającą PIN
            try:
                await update.message.delete()
            except Exception:
                # Ignoruj ewentualne błędy podczas usuwania wiadomości
                pass
            
            return True  # Informacja, że wiadomość została obsłużona jako PIN
        
    return False  # Informacja, że wiadomość nie została obsłużona jako PIN

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Obsługuje komendę /help."""
    await update.message.reply_text(
        "Jak korzystać z bota:\n\n"
        "1. Wyślij link do filmu z YouTube\n"
        "2. Wybierz format (video lub audio) i jakość\n"
        "3. Poczekaj na pobranie pliku\n\n"
        "Bot obsługuje linki z YouTube w formatach:\n"
        "• https://www.youtube.com/watch?v=...\n"
        "• https://youtu.be/..."
    )

async def handle_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Obsługuje linki do YouTube."""
    user_id = update.effective_user.id
    message_text = update.message.text
    
    # Najpierw sprawdź, czy wiadomość jest obsługiwana jako PIN
    pin_handled = await handle_pin(update, context)
    if pin_handled:
        return
    
    # Sprawdź, czy użytkownik jest autoryzowany
    if user_id not in authorized_users:
        # Zapisz URL w danych użytkownika
        context.user_data["pending_url"] = message_text
        
        # Poproś o podanie kodu PIN
        await update.message.reply_text(
            "🔒 Wymagane uwierzytelnienie!\n\n"
            "Proszę podaj 8-cyfrowy kod PIN, aby uzyskać dostęp."
        )
        
        # Ustaw stan oczekiwania na PIN
        context.user_data["awaiting_pin"] = True
        return
    
    # Sprawdź, czy URL jest prawidłowy
    if not message_text.startswith(('https://www.youtube.com/', 'https://youtu.be/')):
        await update.message.reply_text("Nieprawidłowy URL. Podaj link do filmu na YouTube.")
        return
    
    # Sprawdź, czy użytkownik jest zablokowany
    if time.time() < block_until[user_id]:
        remaining_time = int(block_until[user_id] - time.time())
        minutes = remaining_time // 60
        seconds = remaining_time % 60
        
        await update.message.reply_text(
            f"Dostęp zablokowany z powodu zbyt wielu nieudanych prób. "
            f"Spróbuj ponownie za {minutes} min {seconds} s."
        )
        return
    
    # Przetwórz link do YouTube
    await process_youtube_link(update, context, message_text)

async def process_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Przetwarza link do YouTube po autoryzacji PIN-em."""
    # Wyślij wiadomość o pobieraniu informacji
    progress_message = await update.message.reply_text("Pobieranie informacji o filmie...")
    
    # Pobierz informacje o filmie
    info = get_video_info(url)
    if not info:
        await progress_message.edit_text("Wystąpił błąd podczas pobierania informacji o filmie.")
        return
    
    title = info.get('title', 'Nieznany tytuł')
    
    # Przygotuj opcje
    keyboard = [
        [InlineKeyboardButton("🎬 Najlepsza jakość video", callback_data=f"dl_video_best_{url}")],
        [InlineKeyboardButton("🎵 Audio (MP3)", callback_data=f"dl_audio_mp3_{url}")],
        [InlineKeyboardButton("🎵 Audio (M4A)", callback_data=f"dl_audio_m4a_{url}")],
        [InlineKeyboardButton("🎵 Audio (FLAC)", callback_data=f"dl_audio_flac_{url}")],
        [InlineKeyboardButton("📝 Transkrypcja audio", callback_data=f"transcribe_{url}")],
        [InlineKeyboardButton("📝 Transkrypcja + Podsumowanie", callback_data=f"transcribe_summary_{url}")],
        [InlineKeyboardButton("📋 Lista formatów", callback_data=f"formats_{url}")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Aktualizuj wiadomość z opcjami
    await progress_message.edit_text(
        f"🎬 *{title}*\n\nWybierz format do pobrania:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Obsługuje wszystkie wywołania zwrotne."""
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("dl_"):
        _, type, format, url = data.split('_', 3)
        await download_file(update, context, type, format, url)
    elif data.startswith("transcribe_summary_"):
        _, _, url = data.split('_', 2)
        await show_summary_options(update, context, url)
    elif data.startswith("summary_option_"):
        _, _, option, url = data.split('_', 3)
        await download_file(update, context, "audio", "mp3", url, transcribe=True, summary=True, summary_type=int(option))
    elif data.startswith("transcribe_"):
        _, url = data.split('_', 1)
        await download_file(update, context, "audio", "mp3", url, transcribe=True)
    elif data.startswith("formats_"):
        _, url = data.split('_', 1)
        await handle_formats_list(update, context, url)
    elif data.startswith("back_"):
        _, url = data.split('_', 1)
        await back_to_main_menu(update, context, url)

async def download_file(update: Update, context: ContextTypes.DEFAULT_TYPE, type, format, url, transcribe=False, summary=False, summary_type=None):
    """Pobiera plik i wysyła go użytkownikowi."""
    query = update.callback_query
    
    # Wyświetl informację o rozpoczęciu pobierania
    await query.edit_message_text("Rozpoczynam pobieranie... To może chwilę potrwać.")
    
    # Utwórz katalog dla tego czatu
    chat_id = update.effective_chat.id
    chat_download_path = os.path.join(DOWNLOAD_PATH, str(chat_id))
    os.makedirs(chat_download_path, exist_ok=True)
    
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    # Pobierz informacje o filmie
    info = get_video_info(url)
    if not info:
        await query.edit_message_text("Wystąpił błąd podczas pobierania informacji o filmie.")
        return
    
    title = info.get('title', 'Nieznany tytuł')
    sanitized_title = sanitize_filename(title)
    output_path = os.path.join(chat_download_path, f"{current_date} {sanitized_title}")
    
    # Przygotuj opcje pobierania
    ydl_opts = {
        'outtmpl': f"{output_path}.%(ext)s",
        'quiet': True,
        'no_warnings': True,
    }
    
    # Ustaw format audio/video
    if type == "audio" or transcribe:
        # Dla transkrypcji zawsze pobieramy jako mp3
        audio_format_to_use = "mp3" if transcribe else format
        
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': audio_format_to_use,
                'preferredquality': '192',
            }],
        })
    elif type == "video":
        if format == "best":
            ydl_opts['format'] = 'best'
        else:
            ydl_opts['format'] = format
    
    try:
        # Pobierz plik
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        # Znajdź pobrany plik
        downloaded_file_path = None
        for file in os.listdir(chat_download_path):
            full_path = os.path.join(chat_download_path, file)
            if sanitized_title in file and full_path.startswith(output_path):
                downloaded_file_path = full_path
                break
        
        if not downloaded_file_path:
            await query.edit_message_text("Nie można znaleźć pobranego pliku.")
            return
        
        # Jeśli potrzebna transkrypcja
        if transcribe:
            await query.edit_message_text("Pobieranie zakończone. Rozpoczynam transkrypcję audio...")
            
            # Sprawdź, czy istnieje klucz API
            if not CONFIG["GROQ_API_KEY"]:
                await query.edit_message_text(
                    "Błąd: Brak klucza API do transkrypcji w pliku konfiguracyjnym.\n"
                    f"Dodaj klucz GROQ_API_KEY w pliku {CONFIG_FILE_PATH}."
                )
                return
            
            # Wykonaj transkrypcję
            transcript_path = transcribe_mp3_file(downloaded_file_path, chat_download_path)
            
            if not transcript_path or not os.path.exists(transcript_path):
                await query.edit_message_text("Wystąpił błąd podczas transkrypcji.")
                return
            
            # Jeśli potrzebne podsumowanie
            if summary:
                # Sprawdź, czy istnieje klucz API Claude
                if not CONFIG["CLAUDE_API_KEY"]:
                    await query.edit_message_text(
                        "Błąd: Brak klucza API Claude w pliku konfiguracyjnym.\n"
                        f"Dodaj klucz CLAUDE_API_KEY w pliku {CONFIG_FILE_PATH}."
                    )
                    return
                
                await query.edit_message_text("Transkrypcja zakończona. Generuję podsumowanie...")
                
                # Wczytaj transkrypcję
                with open(transcript_path, 'r', encoding='utf-8') as f:
                    transcript_text = f.read()
                
                # Usuń nagłówek markdown jeśli istnieje
                if transcript_text.startswith('# '):
                    transcript_text = '\n'.join(transcript_text.split('\n')[2:])
                
                # Generuj podsumowanie
                summary_text = generate_summary(transcript_text, summary_type)
                
                if not summary_text:
                    await query.edit_message_text("Wystąpił błąd podczas generowania podsumowania.")
                    return
                
                # Zapisz podsumowanie
                summary_path = os.path.join(chat_download_path, f"{sanitized_title}_summary.md")
                with open(summary_path, 'w', encoding='utf-8') as f:
                    summary_types = {
                        1: "Krótkie podsumowanie",
                        2: "Szczegółowe podsumowanie",
                        3: "Podsumowanie w punktach",
                        4: "Podział zadań na osoby"
                    }
                    summary_type_name = summary_types.get(summary_type, "Podsumowanie")
                    f.write(f"# {title} - {summary_type_name}\n\n")
                    f.write(summary_text)
                
                # Wyślij podsumowanie jako wiadomość tekstową
                with open(summary_path, 'r', encoding='utf-8') as f:
                    summary_content = f.read()
                    # Usuwamy nagłówek Markdown
                    if summary_content.startswith('#'):
                        summary_lines = summary_content.split('\n')
                        summary_content = '\n'.join(summary_lines[2:]) if len(summary_lines) > 2 else '\n'.join(summary_lines[1:])
                    
                    # Dodajemy nagłówek jako część wiadomości
                    summary_types = {
                        1: "Krótkie podsumowanie",
                        2: "Szczegółowe podsumowanie",
                        3: "Podsumowanie w punktach",
                        4: "Podział zadań na osoby"
                    }
                    summary_type_name = summary_types.get(summary_type, "Podsumowanie")
                    
                    # Dzielimy wiadomość jeśli jest za długa (limit Telegrama to około 4096 znaków)
                    max_length = 4000
                    message_parts = []
                    current_part = f"📋 *{title} - {summary_type_name}*\n\n"
                    
                    for line in summary_content.split('\n'):
                        if len(current_part) + len(line) + 2 > max_length:
                            message_parts.append(current_part)
                            current_part = line + '\n'
                        else:
                            current_part += line + '\n'
                    
                    if current_part:
                        message_parts.append(current_part)
                    
                    # Wysyłamy części wiadomości
                    for i, part in enumerate(message_parts):
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=part,
                            parse_mode='Markdown'
                        )
            
            else:
                # Standardowa transkrypcja bez podsumowania
                # Wyślij plik z transkrypcją
                await query.edit_message_text("Transkrypcja zakończona. Wysyłanie pliku...")
                
                with open(transcript_path, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=f,
                        filename=os.path.basename(transcript_path),
                        caption=f"📝 Transkrypcja: {title}"
                    )
                
                # Usuń pliki po wysłaniu
                try:
                    os.remove(downloaded_file_path)
                    os.remove(transcript_path)
                    # Usuń inne pliki transkrypcji części
                    for f in os.listdir(chat_download_path):
                        if f.startswith(f"{sanitized_title}_part") and f.endswith("_transcript.txt"):
                            os.remove(os.path.join(chat_download_path, f))
                except Exception as e:
                    logging.error(f"Błąd podczas usuwania plików: {e}")
                
                await query.edit_message_text("✅ Pliki zostały wysłane!")
            
        else:
            # Standardowe pobieranie (bez transkrypcji)
            await query.edit_message_text("Pobieranie zakończone. Wysyłanie pliku...")
            
            # Wyślij plik
            with open(downloaded_file_path, 'rb') as f:
                if type == "audio":
                    await context.bot.send_audio(
                        chat_id=chat_id,
                        audio=f,
                        title=title,
                        caption=f"🎵 {title}"
                    )
                else:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=f,
                        caption=f"🎬 {title}"
                    )
            
            # Usuń plik po wysłaniu
            os.remove(downloaded_file_path)
            
            await query.edit_message_text("✅ Plik został wysłany!")
        
    except Exception as e:
        await query.edit_message_text(f"Wystąpił błąd: {str(e)}")

async def handle_formats_list(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Wyświetla listę formatów."""
    query = update.callback_query
    
    # Pobierz informacje o filmie
    info = get_video_info(url)
    if not info:
        await query.edit_message_text("Wystąpił błąd podczas pobierania informacji o filmie.")
        return
    
    title = info.get('title', 'Nieznany tytuł')
    
    # Przygotuj listę formatów
    video_formats = []
    audio_formats = []
    
    for format in info.get('formats', []):
        format_id = format.get('format_id', 'N/A')
        ext = format.get('ext', 'N/A')
        resolution = format.get('resolution', 'N/A')
        
        if format.get('vcodec') == 'none':
            if len(audio_formats) < 5:  # Limit do 5 formatów audio
                audio_formats.append({
                    'id': format_id,
                    'desc': f"{format_id}: {ext}, {resolution}"
                })
        else:
            if len(video_formats) < 5:  # Limit do 5 formatów video
                video_formats.append({
                    'id': format_id,
                    'desc': f"{format_id}: {ext}, {resolution}"
                })
    
    # Przygotuj klawiaturę
    keyboard = []
    
    # Formaty video
    for format in video_formats:
        keyboard.append([InlineKeyboardButton(f"🎬 {format['desc']}", callback_data=f"dl_video_{format['id']}_{url}")])
    
    # Formaty audio
    for format in audio_formats:
        keyboard.append([InlineKeyboardButton(f"🎵 {format['desc']}", callback_data=f"dl_audio_format_{format['id']}_{url}")])
    
    # Przycisk powrotu
    keyboard.append([InlineKeyboardButton("⬅️ Powrót", callback_data=f"back_{url}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"Formaty dla: {title}\n\nWybierz format:",
        reply_markup=reply_markup
    )

async def show_summary_options(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Wyświetla opcje podsumowania."""
    query = update.callback_query
    
    # Pobierz informacje o filmie
    info = get_video_info(url)
    if not info:
        await query.edit_message_text("Wystąpił błąd podczas pobierania informacji o filmie.")
        return
    
    title = info.get('title', 'Nieznany tytuł')
    
    # Przygotuj opcje
    keyboard = [
        [InlineKeyboardButton("1️⃣ Krótkie podsumowanie", callback_data=f"summary_option_1_{url}")],
        [InlineKeyboardButton("2️⃣ Szczegółowe podsumowanie", callback_data=f"summary_option_2_{url}")],
        [InlineKeyboardButton("3️⃣ Podsumowanie w punktach", callback_data=f"summary_option_3_{url}")],
        [InlineKeyboardButton("4️⃣ Podział zadań na osoby", callback_data=f"summary_option_4_{url}")],
        [InlineKeyboardButton("⬅️ Powrót", callback_data=f"back_{url}")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Aktualizuj wiadomość z opcjami
    await query.edit_message_text(
        f"📝 *{title}*\n\nWybierz rodzaj podsumowania:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Wraca do głównego menu."""
    query = update.callback_query
    
    # Pobierz informacje o filmie
    info = get_video_info(url)
    if not info:
        await query.edit_message_text("Wystąpił błąd podczas pobierania informacji o filmie.")
        return
    
    title = info.get('title', 'Nieznany tytuł')
    
    # Przygotuj opcje
    keyboard = [
        [InlineKeyboardButton("🎬 Najlepsza jakość video", callback_data=f"dl_video_best_{url}")],
        [InlineKeyboardButton("🎵 Audio (MP3)", callback_data=f"dl_audio_mp3_{url}")],
        [InlineKeyboardButton("🎵 Audio (M4A)", callback_data=f"dl_audio_m4a_{url}")],
        [InlineKeyboardButton("🎵 Audio (FLAC)", callback_data=f"dl_audio_flac_{url}")],
        [InlineKeyboardButton("📝 Transkrypcja audio", callback_data=f"transcribe_{url}")],
        [InlineKeyboardButton("📝 Transkrypcja + Podsumowanie", callback_data=f"transcribe_summary_{url}")],
        [InlineKeyboardButton("📋 Lista formatów", callback_data=f"formats_{url}")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"🎬 *{title}*\n\nWybierz format do pobrania:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

def generate_summary(transcript_text, summary_type):
    """
    Generuje podsumowanie transkrypcji przy użyciu API Claude (Haiku).
    
    Parametry:
    - transcript_text: tekst transkrypcji
    - summary_type: rodzaj podsumowania (1-4)
    
    Zwraca:
    - tekst podsumowania lub None w przypadku błędu
    """
    api_key = get_claude_api_key()
    if not api_key:
        logging.error("Nie można odczytać klucza API Claude z pliku api_key.md.")
        return None
    
    # Wybierz odpowiedni prompt w zależności od typu podsumowania
    prompts = {
        1: "Napisz krótkie podsumowanie następującego tekstu:",
        2: "Napisz szczegółowe i rozbudowane podsumowanie następującego tekstu:",
        3: "Przygotuj podsumowanie w formie punktów (bullet points) następującego tekstu:",
        4: "Przygotuj podział zadań na osoby na podstawie następującego tekstu:"
    }
    
    selected_prompt = prompts.get(summary_type, prompts[1])  # Domyślnie krótkie podsumowanie
    
    # Przygotuj żądanie do API Claude
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    
    # Przygotuj dane do żądania
    data = {
        "model": "claude-3-haiku-20240307",
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": f"{selected_prompt}\n\n{transcript_text}"
            }
        ]
    }
    
    try:
        # Wyślij żądanie do API
        response = requests.post(url, headers=headers, json=data)
        
        if response.status_code == 200:
            result = response.json()
            
            # Struktura odpowiedzi Claude API
            summary = ""
            if "content" in result:
                for content_item in result["content"]:
                    if content_item.get("type") == "text":
                        summary += content_item.get("text", "")
            
            return summary
        else:
            logging.error(f"Błąd API Claude: {response.status_code}")
            logging.error(response.text)
            return None
    except Exception as e:
        logging.error(f"Błąd podczas generowania podsumowania: {e}")
        return None

def main():
    # Utwórz aplikację bota
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Zarejestruj handlery
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    
    # Handler do obsługi wiadomości tekstowych (w tym PIN i linki)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_youtube_link))
    
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Uruchom bota
    application.run_polling()

if __name__ == "__main__":
    main()
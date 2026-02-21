"""
CLI module for YouTube Downloader Telegram Bot.

Handles command-line interface and interactive curses menu.
"""

import argparse
import curses

from bot.downloader import (
    get_video_info,
    download_youtube_video,
    is_valid_audio_format,
    is_valid_ytdlp_format_id,
    validate_url,
)


SUPPORTED_AUDIO_FORMATS = ("mp3", "m4a", "wav", "flac", "ogg", "opus", "vorbis")


def show_help():
    """Displays help information for the script."""
    print("YouTube Downloader - tool for downloading YouTube videos")
    print("\nUsage:")
    print("  python main.py [options]")
    print("\nOptions:")
    print("  --help                  Show this help information")
    print("  --cli                   Run in command line mode (no interactive menu)")
    print("  --url <URL>             YouTube video URL")
    print("  --list-formats          Show available formats without downloading")
    print("  --format <ID>           Specify format to download (format ID from list)")
    print("  --format auto           Automatically select best quality")
    print("  --audio-only            Download audio track only (default mp3)")
    print(f"  --audio-format <FORMAT> Specify audio format ({', '.join(SUPPORTED_AUDIO_FORMATS)})")
    print("  --audio-quality <QUALITY> Specify audio quality (0-9 for vorbis/opus, 0-330 for mp3)")
    print("\nExamples:")
    print("  python main.py                                                 # run interactive menu")
    print("  python main.py --cli --url https://www.youtube.com/watch?v=dQw4w9WgXcQ --audio-only")
    print("\nDescription:")
    print("  Program displays available video formats, allows selecting specific format")
    print("  and shows download progress in real-time. You can also download")
    print(f"  only audio track in various formats ({', '.join(SUPPORTED_AUDIO_FORMATS)}).")


def parse_arguments():
    """Parses command line arguments using argparse."""
    parser = argparse.ArgumentParser(description="YouTube Downloader - tool for downloading YouTube videos")
    parser.add_argument("--cli", action="store_true", help="Run in command line mode (no interactive menu)")
    parser.add_argument("--url", help="YouTube video URL")
    parser.add_argument("--list-formats", action="store_true", help="Show available formats without downloading")
    parser.add_argument("--format", help="Specify format to download (format ID from list)")
    parser.add_argument("--audio-only", action="store_true", help="Download audio track only")
    parser.add_argument(
        "--audio-format",
        default="mp3",
        help=f"Specify audio format ({', '.join(SUPPORTED_AUDIO_FORMATS)})",
    )
    parser.add_argument("--audio-quality", default="192", help="Specify audio quality")

    return parser.parse_args()


def curses_main(stdscr):
    """Main function for interactive curses menu."""
    # Terminal configuration
    curses.curs_set(0)
    stdscr.clear()
    stdscr.refresh()

    # Color definitions
    curses.start_color()
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)  # Normal text
    curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)  # Highlight
    curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)  # Headers

    # First menu - ask for URL
    stdscr.addstr(0, 0, "YouTube Downloader", curses.color_pair(3) | curses.A_BOLD)
    stdscr.addstr(2, 0, "Enter YouTube video URL:", curses.color_pair(1))
    stdscr.addstr(3, 0, "> ", curses.color_pair(1))
    stdscr.refresh()

    # Enable cursor visibility
    curses.curs_set(1)

    # Get URL from user
    curses.echo()
    url = stdscr.getstr(3, 2, 100).decode('utf-8')
    curses.noecho()
    curses.curs_set(0)

    # Validate URL
    if not validate_url(url):
        stdscr.addstr(5, 0, "Error: Invalid URL. Provide a YouTube video link.", curses.color_pair(1))
        stdscr.addstr(7, 0, "Press any key to exit...", curses.color_pair(1))
        stdscr.refresh()
        stdscr.getch()
        return

    # Get video info
    stdscr.clear()
    stdscr.addstr(0, 0, "Getting video information...", curses.color_pair(1))
    stdscr.refresh()

    video_info = get_video_info(url)
    if not video_info:
        stdscr.addstr(2, 0, "Error getting video information.", curses.color_pair(1))
        stdscr.addstr(4, 0, "Press any key to exit...", curses.color_pair(1))
        stdscr.refresh()
        stdscr.getch()
        return

    # Prepare format selection menu
    stdscr.clear()
    title = video_info.get('title', 'Unknown title')
    stdscr.addstr(0, 0, f"Video: {title[:50]}{'...' if len(title) > 50 else ''}", curses.color_pair(3) | curses.A_BOLD)
    stdscr.addstr(2, 0, "Available video formats:", curses.color_pair(3))

    # Get formats
    video_formats = []
    audio_formats = []

    for format in video_info.get('formats', []):
        format_id = format.get('format_id', 'N/A')
        ext = format.get('ext', 'N/A')
        resolution = format.get('resolution', 'N/A')
        filesize = f"{format.get('filesize', 0)/1024/1024:.1f}MB" if format.get('filesize') else 'N/A'
        notes = format.get('format_note', '')

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

    # Audio conversion formats
    audio_conversion_formats = [
        {'id': 'mp3_convert', 'desc': "Convert to MP3 (default format)"},
        {'id': 'm4a_convert', 'desc': "Convert to M4A (AAC format)"},
        {'id': 'wav_convert', 'desc': "Convert to WAV"},
        {'id': 'flac_convert', 'desc': "Convert to FLAC (lossless)"},
        {'id': 'opus_convert', 'desc': "Convert to Opus"},
        {'id': 'vorbis_convert', 'desc': "Convert to Vorbis"}
    ]

    # All options in one list
    all_options = []
    all_options.append({'id': 'best', 'desc': "Best available quality (automatic selection)"})
    all_options.extend(video_formats)
    all_options.append({'id': 'separator1', 'desc': "----- Available audio formats -----"})
    all_options.extend(audio_formats)
    all_options.append({'id': 'separator2', 'desc': "----- Audio conversion formats -----"})
    all_options.extend(audio_conversion_formats)

    # Display menu
    current_pos = 0
    page_size = curses.LINES - 6
    offset = 0

    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, f"Video: {title[:50]}{'...' if len(title) > 50 else ''}", curses.color_pair(3) | curses.A_BOLD)
        stdscr.addstr(2, 0, "Select format to download (use arrows and Enter):", curses.color_pair(1))

        # Display options with pagination
        for i in range(min(page_size, len(all_options) - offset)):
            idx = i + offset
            option = all_options[idx]

            # Separator - display only, not selectable
            if option['id'].startswith('separator'):
                stdscr.addstr(i + 4, 0, option['desc'], curses.color_pair(3))
                continue

            # Highlight currently selected option
            if idx == current_pos:
                stdscr.addstr(i + 4, 0, option['desc'], curses.color_pair(2))
            else:
                stdscr.addstr(i + 4, 0, option['desc'], curses.color_pair(1))

        # Navigation info
        footer_y = min(page_size, len(all_options) - offset) + 5
        stdscr.addstr(footer_y, 0, "Up/Down: Navigate  Enter: Select  q: Exit", curses.color_pair(1))
        stdscr.addstr(footer_y + 1, 0, f"Page {offset // page_size + 1}/{(len(all_options) - 1) // page_size + 1}", curses.color_pair(1))

        stdscr.refresh()

        # Key handling
        key = stdscr.getch()

        if key == curses.KEY_UP:
            current_pos -= 1
            while current_pos >= 0 and all_options[current_pos]['id'].startswith('separator'):
                current_pos -= 1

            if current_pos < 0:
                current_pos = len(all_options) - 1
                while current_pos >= 0 and all_options[current_pos]['id'].startswith('separator'):
                    current_pos -= 1

            if current_pos < offset:
                offset = (current_pos // page_size) * page_size

        elif key == curses.KEY_DOWN:
            current_pos += 1
            while current_pos < len(all_options) and all_options[current_pos]['id'].startswith('separator'):
                current_pos += 1

            if current_pos >= len(all_options):
                current_pos = 0
                while current_pos < len(all_options) and all_options[current_pos]['id'].startswith('separator'):
                    current_pos += 1

            if current_pos >= offset + page_size:
                offset = (current_pos // page_size) * page_size

        elif key == curses.KEY_NPAGE:  # Page Down
            offset += page_size
            if offset >= len(all_options):
                offset = 0
            current_pos = offset
            while current_pos < len(all_options) and all_options[current_pos]['id'].startswith('separator'):
                current_pos += 1

        elif key == curses.KEY_PPAGE:  # Page Up
            offset -= page_size
            if offset < 0:
                offset = max(0, ((len(all_options) - 1) // page_size) * page_size)
            current_pos = offset
            while current_pos < len(all_options) and all_options[current_pos]['id'].startswith('separator'):
                current_pos += 1

        elif key == ord('\n'):  # Enter
            selected = all_options[current_pos]
            break

        elif key == ord('q') or key == ord('Q'):
            return

    # Process selected option
    stdscr.clear()
    stdscr.addstr(0, 0, f"Video: {title}", curses.color_pair(3) | curses.A_BOLD)
    stdscr.addstr(2, 0, f"Selected: {selected['desc']}", curses.color_pair(1))
    stdscr.addstr(4, 0, "Starting download...", curses.color_pair(1))
    stdscr.refresh()

    # Close curses mode to display download progress normally
    curses.endwin()

    # Analyze selected option and start download
    if selected['id'] == 'best':
        print(f"Downloading best quality for: {title}")
        download_youtube_video(url)
    elif selected['id'].endswith('_convert'):
        audio_format = selected['id'].split('_')[0]
        print(f"Downloading and converting to {audio_format} format for: {title}")
        download_youtube_video(url, None, True, audio_format, '192')
    else:
        print(f"Downloading format {selected['id']} for: {title}")
        download_youtube_video(url, selected['id'])

    print("\nDownload completed.")
    input("Press Enter to exit...")


def cli_mode(args):
    """Command line mode."""
    if not args.url:
        show_help()
        return

    if not validate_url(args.url):
        return

    if args.audio_format and not is_valid_audio_format(args.audio_format):
        print(f"Error: Unsupported audio format: {args.audio_format}")
        print(f"Supported audio formats: {', '.join(SUPPORTED_AUDIO_FORMATS)}")
        return

    if args.format and not args.list_formats and not is_valid_ytdlp_format_id(args.format):
        print(f"Error: Unsupported format id: {args.format}")
        print("Use --list-formats to see available format IDs.")
        return

    if args.list_formats:
        info = get_video_info(args.url)
        if info:
            title = info.get('title', 'Unknown title')
            print(f"Title: {title}")
            print("\nAvailable formats:")
            print("-" * 80)
            print(f"{'ID':<5} {'Extension':<10} {'Resolution':<15} {'Size':<10} {'Audio only':<10} {'Notes':<20}")
            print("-" * 80)

            for format in info.get('formats', []):
                format_id = format.get('format_id', 'N/A')
                ext = format.get('ext', 'N/A')
                resolution = format.get('resolution', 'N/A')
                filesize = f"{format.get('filesize', 0)/1024/1024:.1f}MB" if format.get('filesize') else 'N/A'
                audio_only = "Yes" if format.get('vcodec') == 'none' else "No"
                notes = format.get('format_note', '')

                print(f"{format_id:<5} {ext:<10} {resolution:<15} {filesize:<10} {audio_only:<10} {notes:<20}")

            print("\nAvailable audio conversion formats:")
            print("-" * 40)
            print("mp3    - MP3 format (default)")
            print("m4a    - AAC format")
            print("wav    - WAV format")
            print("flac   - FLAC format (lossless)")
            print("opus   - Opus format")
            print("vorbis - Vorbis format")
    else:
        download_youtube_video(
            args.url,
            args.format,
            args.audio_only,
            args.audio_format,
            args.audio_quality
        )

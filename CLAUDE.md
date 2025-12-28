# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a YouTube downloader Telegram bot written in Python that combines video/audio downloading with AI-powered transcription and summarization capabilities. The entire application is contained in a single file (`youtube_downloader_telegram.py`) with a monolithic architecture.

## Core Architecture

### Single-File Application Structure
- **Main entry point**: `youtube_downloader_telegram.py` (25,000+ lines)
- **Monolithic design**: All functionality in one file including:
  - Telegram bot handlers and UI logic
  - YouTube downloading via yt-dlp
  - Audio transcription via Groq API (Whisper Large v3)
  - Text summarization via Claude API (Haiku model)
  - Authentication and security features
  - Background cleanup and monitoring

### Key Components
1. **Configuration Management**: Multi-source config loading (env vars > .env > api_key.md > defaults)
2. **Security Layer**: PIN authentication, rate limiting, URL validation, file size limits
3. **Download Engine**: yt-dlp integration with progress tracking and format selection
4. **AI Processing**: Audio chunking, transcription, and summarization pipelines
5. **File Management**: Automated cleanup, disk monitoring, user-specific directories

## Development Commands

### Setup and Configuration
```bash
# Interactive setup (recommended for first time)
python setup_config.py

# Manual dependency installation
pip install yt-dlp mutagen python-telegram-bot requests

# System dependency (required)
# Ubuntu/Debian: sudo apt install ffmpeg
# macOS: brew install ffmpeg
# Windows: Download from ffmpeg.org and add to PATH
```

### Running the Application
```bash
# Start Telegram bot (primary mode)
python youtube_downloader_telegram.py

# CLI mode (limited functionality)
python youtube_downloader_telegram.py --cli --url "https://youtube.com/watch?v=..."
```

### Testing
```bash
# Standalone security tests (no dependencies)
python test_security_standalone.py

# Full security tests (requires main app imports)
python test_security.py

# No formal test runner - uses manual verification
```

### Configuration Management
```bash
# Check configuration status
python -c "from youtube_downloader_telegram import load_config; print(load_config())"

# Validate configuration file
python setup_config.py
```

## Configuration Requirements

### Required API Keys (in api_key.md or environment variables)
- `TELEGRAM_BOT_TOKEN`: Bot token from @BotFather
- `GROQ_API_KEY`: For audio transcription (Whisper)
- `CLAUDE_API_KEY`: For text summarization
- `PIN_CODE`: 8-digit numeric PIN for user authentication

### Configuration Priority Order
1. Environment variables (highest)
2. `.env` file
3. `api_key.md` file
4. Default values (lowest)

## Language Policy

**Critical**: This project follows a strict language policy:
- **User-facing content**: All bot messages, UI, and user interactions in Polish
- **Technical content**: All code, comments, documentation, and git commits in English
- **Files affected**: When editing `youtube_downloader_telegram.py`, maintain Polish for user messages and English for technical code

## Key Technical Patterns

### Error Handling
- All API calls wrapped in try/except blocks
- User-friendly error messages in Polish
- Comprehensive logging for debugging
- Graceful degradation when services unavailable

### Security Implementation
- Rate limiting: 10 requests/minute per user with timestamp tracking
- File size validation before download (1GB limit)
- URL validation with domain whitelist
- PIN authentication with 3-attempt lockout (15 minutes)
- Automatic cleanup of old files (>24 hours)

### Memory Management
- User sessions stored in memory (lost on restart)
- Large files automatically chunked for processing
- Background threading for cleanup operations
- Disk space monitoring with warnings

### AI Integration Patterns
- **Transcription**: Files >25MB split at silence points, processed separately, then merged
- **Summarization**: 4 different summary types with prompt templates
- **Error recovery**: Fallback handling when AI services fail

## File Structure and Patterns

```
ytdown/
├── youtube_downloader_telegram.py    # Main application (edit this for features)
├── setup_config.py                   # Configuration utility
├── test_security*.py                 # Security test files
├── api_key.md                        # Config file (never commit!)
├── .gitignore                        # Comprehensive ignore rules
├── TODO.md                           # Development roadmap
└── downloads/[chat_id]/              # User-specific download directories
```

## Development Guidelines

### Adding New Features
1. All new functionality goes in `youtube_downloader_telegram.py`
2. Maintain the existing async/await pattern for Telegram handlers
3. Add appropriate error handling with Polish user messages
4. Update rate limiting logic if adding resource-intensive features
5. Consider disk space impact for new file operations

### Modifying AI Integration
- **Transcription**: Modify `transcribe_audio_groq()` function
- **Summarization**: Update prompt templates in summarization handlers
- **Authentication**: Changes go in PIN validation and session management

### Security Considerations
- Never commit `api_key.md` or similar config files
- Validate all user inputs before processing
- Maintain rate limiting for new endpoints
- Test security changes with `test_security_standalone.py`

### Adding Dependencies
- No package manager files exist - dependencies are manually managed
- Update README.md installation instructions when adding new dependencies
- Consider impact on single-file architecture

## Current Status and TODOs

- **Phase 1-2 Complete**: Security, cleanup, monitoring features implemented
- **Next priorities**: JSON persistence, download history, playlist support
- **See TODO.md**: Comprehensive roadmap with phases and timelines

## Deployment Notes

- Single Python file deployment
- No database required (file-based storage)
- Runs on Linux/Windows/macOS
- Suitable for VPS, Raspberry Pi, or local deployment
- Requires ffmpeg system dependency
# AnimePahe Universal Extractor API 🎬

A blazing-fast, concurrent API built with **FastAPI** and **Playwright** to seamlessly extract search results, anime information, episodes, and direct raw streaming M3U8/MP4 links from AnimePahe.

Includes built-in Cloudflare bypass logic, ad-network blocking, and deep API pagination search engines.

## 🚀 Features

- **Search Anime**: Search the AnimePahe directory directly.
- **Latest Episodes**: Fetch currently airing/latest releases.
- **Deep Info Extraction**: Pulls Synopses, Genres, Posters, and Global Tracking IDs (MAL, AniList, Kitsu, ANN).
- **Resolver**: Auto-clicks through Kwik provider tokens to return raw `.m3u8` streams and auto-generates `.mp4` download links with perfectly formatted file names.
- **Docker Ready**: Fully prepared to be deployed to the cloud via Docker containerization.

## 📦 Local Installation

1. **Clone the repository** and navigate to the directory:

   ```bash
   git clone https://github.com/yourusername/animepahe-api.git
   cd animepahe-api
   ```

2. **Install the required Python dependencies**:

   ```bash
   pip install -r requirements.txt
   ```

3. **Install Playwright browser binaries**:

   ```bash
   playwright install chromium
   ```

4. **Run the API**:
   ```bash
   # By default runs on http://localhost:8000
   python main.py
   ```

## ☁️ Cloud Deployment Guide

> **⚠️ IMPORTANT:** Do **NOT** deploy this to Vercel, Netlify, or Serverless platforms.
> Playwright requires a full Chromium browser binary (~300MB) and a long-running background process. Serverless platforms have strict size limits and execution timeouts which will cause this app to crash.

**Recommended Providers:** Render.com, Railway.app, or Koyeb

This repository includes a Dockerfile utilizing Microsoft's official Playwright image. This guarantees all Linux browser dependencies are pre-installed.

### How to deploy on Render.com:

1. Upload this project to your GitHub.
2. Go to Render and create a new **Web Service**.
3. Connect your GitHub repository.
4. Render will automatically detect the `Dockerfile` and build the container perfectly.
5. _(Optional)_ Set the `PORT` environment variable if required by the host.

## 📡 API Endpoints

- `GET /` - Health check.
- `GET /search?q={query}` - Search for an anime.
- `GET /latest?p={page}` - Get recently released episodes.
- `GET /info/{anime_session}` - Get details, global IDs, and first page of episodes.
- `GET /episodes/{anime_session}?p={page}` - Get paginated episodes.
- `GET /resolve/{anime_session}/{episode_session}` - Extract raw video sources, resolutions, Sub/Dub info, and direct MP4 download paths.

## 5. .gitignore

_(Copy and paste this into `.gitignore`. This stops you from accidentally uploading local junk files/browser data to GitHub)._

```text
# Byte-compiled / optimized / DLL files
__pycache__/
*.py[cod]
*$py.class

# Environments
.env
.venv
env/
venv/
ENV/

# Playwright Browser Data (VERY IMPORTANT)
browser_data/
```

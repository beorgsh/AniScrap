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

1. Clone the repository and navigate to the directory:
   ```bash
   git clone https://github.com/yourusername/animepahe-api.git
   cd animepahe-api
   ```

````
2. Install the required Python dependencies:
   ```bash
  pip install -r requirements.txt
````

3. Install Playwright browser binaries:
   ```bash
   playwright install chromium
   ```

```
4. Run the API:
# By default runs on http://localhost:8000
python main.py
```

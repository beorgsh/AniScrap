import re
import os
import httpx
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from playwright.async_api import async_playwright, BrowserContext

BASE_URL = "https://animepahe.si"
IS_HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

# Detect if running on low-resource environment (Render free = 512MB)
IS_LOW_RESOURCE = os.environ.get("LOW_RESOURCE", "true").lower() == "true"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/json,*/*",
    "Referer": BASE_URL,
}

# ─────────────────────────────────────────────
#  Global state
# ─────────────────────────────────────────────
http: httpx.AsyncClient = None
browser_context: BrowserContext = None
playwright_instance = None
cf_cookies: dict = {}
cf_cookie_age: float = 0
cf_lock = asyncio.Lock()

# On free tier: only 1 browser page at a time to avoid OOM
# On paid tier: up to 3 concurrent
KWIK_CONCURRENCY = 1 if IS_LOW_RESOURCE else 3
kwik_semaphore: asyncio.Semaphore = None


# ─────────────────────────────────────────────
#  Lifespan
# ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global http, browser_context, playwright_instance, kwik_semaphore

    kwik_semaphore = asyncio.Semaphore(KWIK_CONCURRENCY)

    http = httpx.AsyncClient(
        headers=HEADERS,
        follow_redirects=True,
        timeout=20.0,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
    )

    playwright_instance = await async_playwright().start()
    browser_context = await playwright_instance.chromium.launch_persistent_context(
        user_data_dir="./browser_data",
        headless=IS_HEADLESS,
        user_agent=HEADERS["User-Agent"],
        viewport={"width": 1280, "height": 720},
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",                        # ⚡ no GPU needed
            "--disable-software-rasterizer",        # ⚡ save CPU
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--js-flags=--max-old-space-size=256",  # ⚡ limit JS heap
        ]
    )

    # Block everything except scripts and XHR globally
    await browser_context.route(
        "**/*",
        lambda route: route.abort()
        if route.request.resource_type in ("image", "stylesheet", "font", "media", "other")
        else route.continue_()
    )

    await refresh_cf_cookies()
    asyncio.create_task(keep_alive())
    asyncio.create_task(cookie_refresher())

    yield

    await http.aclose()
    await browser_context.close()
    await playwright_instance.stop()


# ─────────────────────────────────────────────
#  Cloudflare cookie management
# ─────────────────────────────────────────────
async def refresh_cf_cookies():
    global cf_cookies, cf_cookie_age
    async with cf_lock:
        print("Refreshing Cloudflare cookies...")
        page = await browser_context.new_page()
        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            for _ in range(6):
                title = await page.title()
                if "Just a moment" in title or "Cloudflare" in title:
                    await page.wait_for_timeout(4000)
                else:
                    break
            cookies = await browser_context.cookies()
            cf_cookies = {c["name"]: c["value"] for c in cookies}
            cf_cookie_age = asyncio.get_event_loop().time()
            print(f"CF cookies refreshed: {list(cf_cookies.keys())}")
        except Exception as e:
            print(f"CF cookie refresh error: {e}")
        finally:
            await page.close()


async def ensure_cf_cookies():
    """Refresh only if cookies are older than 30 minutes."""
    now = asyncio.get_event_loop().time()
    if not cf_cookies or (now - cf_cookie_age) > 1800:
        await refresh_cf_cookies()


async def get_cf_headers() -> dict:
    await ensure_cf_cookies()
    cookie_str = "; ".join(f"{k}={v}" for k, v in cf_cookies.items())
    return {**HEADERS, "Cookie": cookie_str}


async def cookie_refresher():
    """Proactively refresh CF cookies every 25 min — never blocks a request."""
    await asyncio.sleep(60)
    while True:
        await asyncio.sleep(25 * 60)
        try:
            await refresh_cf_cookies()
        except Exception as e:
            print(f"Background cookie refresh failed: {e}")


# ─────────────────────────────────────────────
#  HTTP helpers
# ─────────────────────────────────────────────
async def api_get(path: str):
    r = await http.get(
        f"{BASE_URL}{path}",
        headers={**(await get_cf_headers()), "Accept": "application/json"}
    )
    if r.status_code == 403:
        await refresh_cf_cookies()
        r = await http.get(
            f"{BASE_URL}{path}",
            headers={**(await get_cf_headers()), "Accept": "application/json"}
        )
    r.raise_for_status()
    return r.json()


async def html_get_cf(url: str) -> str:
    r = await http.get(url, headers=await get_cf_headers())
    if r.status_code == 403:
        await refresh_cf_cookies()
        r = await http.get(url, headers=await get_cf_headers())
    r.raise_for_status()
    return r.text


# ─────────────────────────────────────────────
#  Kwik resolver — Playwright (JS-rendered m3u8)
# ─────────────────────────────────────────────
async def resolve_kwik(embed_url: str) -> str | None:
    """
    Uses a single browser page to intercept the JS-generated m3u8 request.
    Semaphore limits concurrent pages to avoid OOM on free tier.
    """
    async with kwik_semaphore:
        page = await browser_context.new_page()
        try:
            m3u8_future = asyncio.get_event_loop().create_future()

            def handle_request(request):
                if ".m3u8" in request.url and not m3u8_future.done():
                    m3u8_future.set_result(request.url)

            page.on("request", handle_request)

            await page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in ("image", "stylesheet", "font", "media", "other")
                else route.continue_()
            )

            await page.set_extra_http_headers({"Referer": BASE_URL})
            await page.goto(embed_url, wait_until="domcontentloaded", timeout=15000)

            async def auto_clicker():
                while not m3u8_future.done():
                    try:
                        await page.evaluate(
                            "document.querySelectorAll("
                            "'button, .plyr__poster, video, [class*=play], form, input[type=\"submit\"]'"
                            ").forEach(el => el.click())"
                        )
                    except Exception:
                        pass
                    await asyncio.sleep(0.3)

            click_task = asyncio.create_task(auto_clicker())
            try:
                return await asyncio.wait_for(m3u8_future, timeout=8.0)
            except asyncio.TimeoutError:
                print(f"Kwik timeout for {embed_url}")
                return None
            finally:
                click_task.cancel()

        except Exception as e:
            print(f"Kwik resolve error for {embed_url}: {e}")
            return None
        finally:
            page.remove_listener("request", handle_request)
            await page.close()


# ─────────────────────────────────────────────
#  Download URL builder
# ─────────────────────────────────────────────
def build_download_url(m3u8_url: str | None, filename: str) -> str | None:
    if not m3u8_url:
        return None
    m = re.search(r'https?://([^/]+)/(?:hls/)?stream/([a-zA-Z0-9]+)', m3u8_url)
    if m:
        host = m.group(1)
        stream_id = m.group(2)
        base = re.sub(r'^[^.]+\.', '', host)
        return f"https://{base}/mp4/{stream_id}?file={filename}"
    return None


# ─────────────────────────────────────────────
#  Keep-alive (Render free tier anti-sleep)
# ─────────────────────────────────────────────
async def keep_alive():
    await asyncio.sleep(30)
    port = int(os.environ.get("PORT", 8000))
    while True:
        try:
            async with httpx.AsyncClient() as c:
                await c.get(f"http://localhost:{port}/", timeout=10)
            print("Keep-alive ping ✓")
        except Exception as e:
            print(f"Keep-alive failed: {e}")
        await asyncio.sleep(300)


# ─────────────────────────────────────────────
#  App
# ─────────────────────────────────────────────
app = FastAPI(title="AnimePahe Hybrid API", lifespan=lifespan)


@app.get("/")
async def root():
    return {"message": "AnimePahe Hybrid API — lightweight + Cloudflare ready!"}


@app.get("/search")
async def search(q: str):
    data = await api_get(f"/api?m=search&q={q}")
    return data.get("data", [])


@app.get("/latest")
async def latest(p: int = 1):
    return await api_get(f"/api?m=airing&page={p}")


@app.get("/episodes/{anime_session}")
async def episodes(anime_session: str, p: int = 1, sort: str = "episode_desc"):
    return await api_get(f"/api?m=release&id={anime_session}&sort={sort}&page={p}")


@app.get("/info/{anime_session}")
async def info(anime_session: str, p: int = 1):
    html, eps = await asyncio.gather(
        html_get_cf(f"{BASE_URL}/anime/{anime_session}"),
        api_get(f"/api?m=release&id={anime_session}&sort=episode_desc&page={p}")
    )

    def scrape(label):
        m = re.search(f'<strong>{label}:</strong>.*?<a[^>]*>([^<]+)</a>', html, re.I)
        if m: return m.group(1).strip()
        m = re.search(f'<strong>{label}:</strong>\\s*([^<\\n]+)', html, re.I)
        return re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else "Unknown"

    h1 = re.search(r'<h1[^>]*>\s*<span[^>]*>([^<]+)</span>\s*</h1>', html)
    title_meta = re.search(r'<title>([^<]+)</title>', html)
    title = h1.group(1).strip() if h1 else (
        title_meta.group(1).split(" :: ")[0].strip() if title_meta else "Unknown"
    )
    title = re.sub(r'(?i)\s*Ep\.?\s*\d+(?:-\d+)?|\s*\[.*?\]', '', title).strip()

    poster = (
        re.search(r'<meta\s+property="og:image"\s+content="([^"]+)">', html) or
        re.search(r'<img[^>]+src="([^"]+)"[^>]*class="[^"]*poster', html)
    )
    syn = re.search(r'<div class="anime-synopsis"[^>]*>(.*?)</div>', html, re.DOTALL | re.I)
    yt = re.search(r'youtube\.com/embed/([^"?]+)', html)
    genre_block = re.search(r'<div class="anime-genre">(.*?)</div>', html, re.DOTALL)
    genres = re.findall(r'>([^<]+)</a>', genre_block.group(1)) if genre_block else []

    animepahe_id = None
    has_dub = False
    has_sub = False
    if isinstance(eps, dict) and eps.get("data"):
        animepahe_id = eps["data"][0].get("anime_id")
        for ep in eps["data"]:
            audio = ep.get("audio", "")
            if audio == "eng":
                has_dub = True
            else:
                has_sub = True
            if has_dub and has_sub:
                break

    def safe_int(pattern):
        m = re.search(pattern, html)
        return int(m.group(1)) if m else None

    return {
        "title": title,
        "session": anime_session,
        "poster": poster.group(1) if poster else None,
        "synopsis": re.sub(r'<[^>]+>', '', syn.group(1)).strip() if syn else "Not Available",
        "type": scrape("Type"),
        "status": scrape("Status"),
        "studio": scrape("Studio"),
        "season": scrape("Season"),
        "youtube_trailer": f"https://youtube.com/watch?v={yt.group(1)}" if yt else None,
        "genres": genres,
        "hasDub": has_dub,
        "hasSub": has_sub,
        "ids": {
            "animepahe_id": animepahe_id,
            "mal_id": safe_int(r'myanimelist\.net/anime/(\d+)'),
            "anilist_id": safe_int(r'anilist\.co/anime/(\d+)'),
            "ann_id": safe_int(r'animenewsnetwork\.com/encyclopedia/anime\.php\?id=(\d+)'),
            "kitsu": (re.search(r'kitsu\.io/anime/([^/"\'<>\s]+)', html) or [None, None])[1],
            "anime_planet": (re.search(r'anime-planet\.com/anime/([^/"\'<>\s]+)', html) or [None, None])[1],
        },
        "episodes": eps
    }


@app.get("/resolve/{anime_session}/{episode_session}")
async def resolve(anime_session: str, episode_session: str):

    async def get_embeds():
        html = await html_get_cf(f"{BASE_URL}/play/{anime_session}/{episode_session}")
        results = []
        for m in re.finditer(
            r'<button[^>]+data-src="([^"]+)"[^>]+data-fansub="([^"]*)"[^>]+data-resolution="([^"]*)"[^>]+data-audio="([^"]*)"',
            html
        ):
            embed  = m.group(1)
            fan_sub = m.group(2)
            resolution = m.group(3)
            audio = m.group(4)
            is_dub = audio == "eng"
            results.append({
                "embed": embed,
                "resolution": resolution,
                "fanSub": fan_sub,
                "audio": audio,
                "isDub": is_dub
            })
        return results

    async def get_episode_num():
        chunk = await api_get(
            f"/api?m=release&id={anime_session}&sort=episode_desc&page=1"
        )
        if isinstance(chunk, dict):
            for ep in chunk.get("data", []):
                if ep.get("session") == episode_session:
                    return str(ep.get("episode")), ep.get("anime_id")
        return "Unknown", None

    # ⚡ Fetch embed list + episode number simultaneously via httpx
    embeds, (episode_num, ap_id) = await asyncio.gather(
        get_embeds(),
        get_episode_num()
    )

    # On free tier: resolve only the best quality per audio type
    # to cut browser time from 6 pages → 2 pages
    if IS_LOW_RESOURCE:
        seen = {}
        filtered = []
        # Pick highest resolution per audio type (sub + dub)
        for e in sorted(embeds, key=lambda x: int(x["resolution"]) if x["resolution"].isdigit() else 0, reverse=True):
            key = "dub" if e["isDub"] else "sub"
            if key not in seen:
                seen[key] = True
                filtered.append(e)
        # Also add 720p as fallback for each type
        for e in sorted(embeds, key=lambda x: int(x["resolution"]) if x["resolution"].isdigit() else 0, reverse=True):
            key = f"{('dub' if e['isDub'] else 'sub')}_720"
            if e["resolution"] == "720" and key not in seen:
                seen[key] = True
                filtered.append(e)
        embeds_to_resolve = filtered
    else:
        embeds_to_resolve = embeds

    async def extract(item):
        m3u8 = await resolve_kwik(item["embed"])
        filename = (
            f"AnimePahe_{anime_session}"
            f"_-_{episode_num}"
            f"_{item['resolution']}p"
            f"_{'DUB' if item['isDub'] else item['fanSub'].replace(' ', '')}"
            f".mp4"
        )
        return {
            "url": m3u8,
            "isM3U8": True,
            "embed": item["embed"],
            "resolution": item["resolution"],
            "isDub": item["isDub"],
            "fanSub": item["fanSub"],
            "download": build_download_url(m3u8, filename)
        }

    # On free tier semaphore=1 so this runs sequentially
    # On paid tier semaphore=3 so runs in parallel
    sources = await asyncio.gather(*[extract(e) for e in embeds_to_resolve])

    sub_sources = sorted(
        [s for s in sources if not s["isDub"]],
        key=lambda x: int(x["resolution"]) if x["resolution"].isdigit() else 0,
        reverse=True
    )
    dub_sources = sorted(
        [s for s in sources if s["isDub"]],
        key=lambda x: int(x["resolution"]) if x["resolution"].isdigit() else 0,
        reverse=True
    )

    return {
        "ids": {"animepahe_id": ap_id},
        "session": episode_session,
        "provider": "kwik",
        "episode": episode_num,
        "hasDub": len(dub_sources) > 0,
        "sources": {
            "sub": sub_sources,
            "dub": dub_sources
        }
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    script_name = os.path.splitext(os.path.basename(__file__))[0]
    uvicorn.run(f"{script_name}:app", host="0.0.0.0", port=port, workers=1)

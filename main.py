import json
import asyncio
import re
import os
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI
from playwright.async_api import async_playwright, BrowserContext, Page, Request

# CURRENT ANIMEPAHE DOMAIN
BASE_URL = "https://animepahe.si"
# Dynamic headless mode (Cloud platforms require True, local can be False)
IS_HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

class AnimePahe:
    def __init__(self):
        self.playwright = None
        self.context: Optional[BrowserContext] = None
        
        self.ad_domains =[
            "doubleclick.net", "adservice.google", "popads.net", 
            "propellerads", "exoclick", "ad-score", "clck.ru", 
            "okx.com", "yandex", "mc.yandex.ru", "onclck.com", "bebi.com"
        ]

    async def start(self):
        self.playwright = await async_playwright().start()
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir="./browser_data",
            headless=IS_HEADLESS,
            user_agent=user_agent,
            viewport={"width": 1280, "height": 720},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-popup-blocking",
            ]
        )

        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () =>['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () =>[1, 2, 3]});
            window.open = function() { console.log('Popup blocked'); return null; };
        """)
        
        await self.context.route("**/*", self._intercept_ads)

    async def _intercept_ads(self, route):
        url = route.request.url.lower()
        if any(ad in url for ad in self.ad_domains) or url.endswith((".png", ".jpg", ".gif", ".css", ".woff")):
            await route.abort()
        else:
            await route.continue_()

    async def stop(self):
        if self.context: await self.context.close()
        if self.playwright: await self.playwright.stop()

    async def _safe_goto(self, page, url, referer=None):
        try:
            if referer: await page.set_extra_http_headers({"Referer": referer})
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            
            # Upgraded Wait Loop: Keeps checking if Cloudflare is still active
            for _ in range(4):
                title = await page.title()
                if "Just a moment" in title or "Cloudflare" in title: 
                    print(f"Cloudflare verification active on {url}, Waiting...")
                    await page.wait_for_timeout(5000) 
                else:
                    break
            return response
        except Exception as e: 
            print(f"Navigation Error: {e}")

    # --- NEW HELPER METHODS TO SAFELY BYPASS CLOUDFLARE ---

    async def _fetch_json(self, url: str):
        page = await self.context.new_page()
        try:
            await self._safe_goto(page, url)
            # Safely grab the text out of the document, avoiding HTML wrappers
            content = await page.evaluate("document.body ? document.body.innerText : document.documentElement.innerText")
            return json.loads(content)
        except Exception as e:
            print(f"JSON Parse Blocked: {e}")
            return None
        finally:
            await page.close()

    async def _fetch_html(self, url: str):
        page = await self.context.new_page()
        try:
            await self._safe_goto(page, url)
            return await page.content()
        except Exception as e:
            print(f"HTML Fetch Blocked: {e}")
            return ""
        finally:
            await page.close()

    def _convert_m3u8_to_mp4(self, m3u8_url: Optional[str], generated_filename: str) -> Optional[str]:
        if not m3u8_url: return None
        match = re.search(r'(https?://[^.]+)[^/]*/stream/(.*?)/[^/]+\.m3u8', m3u8_url)
        if match:
            return f"{match.group(1)}.kwik.cx/mp4/{match.group(2)}?file={generated_filename}"
        return None

    # ==========================================
    # API ENDPOINTS NATIVE JSON HOOKS 
    # ==========================================
    
    async def get_latest_episodes(self, page_num: int = 1):
        url = f"{BASE_URL}/api?m=airing&page={page_num}"
        data = await self._fetch_json(url)
        if not data: return {"error": "Cloudflare cache blocked payload. Clear on browser request."}
        return data

    async def search(self, query: str):
        url = f"{BASE_URL}/api?m=search&q={query}"
        data = await self._fetch_json(url)
        if not data: return {"error": "Search payload was Cloudflare blocked."}
        return data.get("data",[])

    async def get_episodes(self, anime_session: str, page_num: int = 1, sort: str = "episode_desc"):
        url = f"{BASE_URL}/api?m=release&id={anime_session}&sort={sort}&page={page_num}"
        data = await self._fetch_json(url)
        if not data: return {"error": "Pagination Chunk block encountered."}
        return data

    async def get_anime_info(self, anime_session: str, page_num: int = 1):
        url = f"{BASE_URL}/anime/{anime_session}"
        content = await self._fetch_html(url)

        if not content or "Just a moment" in content: 
            return {"error": "Cloudflare triggered. Fix once on frontend visually in headless=False cache directory."}

        def scrape_strong(label: str):
            matched = re.search(f'<strong>{label}:</strong>.*?<a[^>]*>([^<]+)</a>', content, re.IGNORECASE)
            if matched: return matched.group(1).strip()
            matched_fallback = re.search(f'<strong>{label}:</strong>\\s*([^<\\n]+)', content, re.IGNORECASE)
            return re.sub(r'<[^>]+>', '', matched_fallback.group(1)).strip() if matched_fallback else "Unknown"

        h1_match = re.search(r'<h1[^>]*>\s*<span[^>]*>([^<]+)</span>\s*</h1>', content)
        if h1_match:
            title_txt = h1_match.group(1).strip()
        else:
            title_meta = re.search(r'<title>([^<]+)</title>', content)
            title_txt = title_meta.group(1).split(" :: ")[0].strip() if title_meta else "Unknown"

        title_txt = re.sub(r'(?i)\s*Ep\.?\s*\d+(?:-\d+)?', '', title_txt)
        title_txt = re.sub(r'(?i)\s*\[.*?\]', '', title_txt).strip()

        poster = re.search(r'<a\s+href="([^"]+)"\s+class="youtube-preview"', content) or \
                 re.search(r'<img[^>]+src="([^"]+)"[^>]*class="[^"]*poster', content) or \
                 re.search(r'<meta\s+property="og:image"\s+content="([^"]+)">', content)
        poster_url = poster.group(1) if poster else None

        syn_block = re.search(r'<div class="anime-synopsis"[^>]*>(.*?)</div>', content, re.DOTALL | re.IGNORECASE)
        synopsis_text = re.sub(r'<[^>]+>', '', syn_block.group(1)).strip() if syn_block else "Not Available"

        yt_match = re.search(r'youtube\.com/embed/([^"?]+)', content)
        
        genres =[]
        genre_tags = re.search(r'<div class="anime-genre">(.*?)</div>', content, re.DOTALL)
        if genre_tags: genres = re.findall(r'>([^<]+)</a>', genre_tags.group(1))

        episodes_chunk = await self.get_episodes(anime_session, page_num=page_num, sort="episode_desc")

        animepahe_id = None
        if isinstance(episodes_chunk, dict) and episodes_chunk.get("data"):
            animepahe_id = episodes_chunk["data"][0].get("anime_id")

        ids = {
            "animepahe_id": animepahe_id,
            "mal_id": int((re.search(r'myanimelist\.net/anime/(\d+)', content) or [0, 0])[1] or 0) or None,
            "anilist_id": int((re.search(r'anilist\.co/anime/(\d+)', content) or [0, 0])[1] or 0) or None,
            "ann_id": int((re.search(r'animenewsnetwork\.com/encyclopedia/anime\.php\?id=(\d+)', content) or [0, 0])[1] or 0) or None,
            "kitsu": (re.search(r'kitsu\.io/anime/([^/"\'<>\s]+)', content) or [None, None])[1],
            "anime_planet": (re.search(r'anime-planet\.com/anime/([^/"\'<>\s]+)', content) or[None, None])[1]
        }

        return {
            "title": title_txt,
            "session": anime_session,
            "poster": poster_url,
            "synopsis": synopsis_text,
            "type": scrape_strong("Type"),
            "status": scrape_strong("Status"),
            "studio": scrape_strong("Studio"),
            "season": scrape_strong("Season"),
            "youtube_trailer": f"https://youtube.com/watch?v={yt_match.group(1)}" if yt_match else None,
            "genres": genres,
            "ids": ids,
            "episodes": episodes_chunk
        }

    # ==========================================
    # BROWSING DRIVER RESOLVER ENDPOINTS 
    # ==========================================
    
    async def _extract_m3u8(self, kwik_url: str):
        page = await self.context.new_page()
        try:
            m3u8_future = asyncio.get_event_loop().create_future()
            
            def handle_request(request: Request):
                if ".m3u8" in request.url and not m3u8_future.done(): 
                    m3u8_future.set_result(request.url)
                    
            page.on("request", handle_request)
            await page.route("**/*.ts", lambda route: route.abort()) 
            await page.set_extra_http_headers({"Referer": BASE_URL})
            await page.goto(kwik_url, wait_until="domcontentloaded", timeout=20000)

            async def auto_clicker():
                while not m3u8_future.done():
                    try: 
                        await page.evaluate("document.querySelectorAll('button, .plyr__poster, video, form, input[type=\"submit\"]').forEach(el => el.click())")
                    except Exception: pass
                    await asyncio.sleep(0.5)
            
            click_task = asyncio.create_task(auto_clicker())
            
            try: return await asyncio.wait_for(m3u8_future, timeout=12.0)
            except asyncio.TimeoutError: return None
            finally: click_task.cancel()
                
        except Exception: return None
        finally:
            page.remove_listener("request", handle_request)
            await page.close()


    async def get_links(self, anime_session: str, episode_session_id: str):
        info_data = await self.get_anime_info(anime_session)
        anime_title = info_data.get("title", anime_session)
        global_ids = info_data.get("ids", {})
        
        anime_title = re.sub(r'(?i)\s*Ep\.?\s*\d+(?:-\d+)?', '', anime_title)
        anime_title = re.sub(r'(?i)\s*\[.*?\]', '', anime_title).strip()

        episode_num = None
        ap_id = None
        
        episodes_chunk = info_data.get("episodes", {})
        last_page = 1

        if isinstance(episodes_chunk, dict):
            last_page = episodes_chunk.get("last_page", 1)
            for ep in episodes_chunk.get("data",[]):
                if ep.get("session") == episode_session_id:
                    episode_num = str(ep.get("episode"))
                    ap_id = ep.get("anime_id")
                    break

        if not episode_num and last_page > 1:
            api_sem = asyncio.Semaphore(5)
            async def fetch_page(p):
                async with api_sem:
                    chunk = await self.get_episodes(anime_session, page_num=p)
                    if isinstance(chunk, dict):
                        for ep in chunk.get("data",[]):
                            if ep.get("session") == episode_session_id:
                                return str(ep.get("episode")), ep.get("anime_id")
                    return None, None

            tasks =[fetch_page(p) for p in range(2, last_page + 1)]
            results = await asyncio.gather(*tasks)
            for res_ep, res_id in results:
                if res_ep:
                    episode_num = res_ep
                    ap_id = res_id
                    break

        if ap_id and not global_ids.get("animepahe_id"):
            global_ids["animepahe_id"] = ap_id

        if not episode_num: episode_num = "Unknown"

        page = await self.context.new_page()
        try:
            play_url = f"{BASE_URL}/play/{anime_session}/{episode_session_id}"
            await self._safe_goto(page, play_url)
            content = await page.content()

            await page.wait_for_selector("#resolutionMenu button", timeout=15000)
            res_btns = await page.locator("#resolutionMenu button").all()
            
            res_data =[]
            for btn in res_btns:
                text = (await btn.inner_text()).strip() 
                embed_url = await btn.get_attribute("data-src")
                audio_type = await btn.get_attribute("data-audio")

                parts = text.split("·")
                fan_sub = parts[0].strip() if len(parts) > 1 else "Unknown"
                resolution_raw = parts[1].strip() if len(parts) > 1 else text
                
                res_search = re.search(r'(\d+)', resolution_raw)
                res_num = res_search.group(1) if res_search else "unknown"
                is_dub = True if (audio_type == "eng" or fan_sub.lower() == "eng") else False
                
                res_data.append({
                    "embed": embed_url,
                    "resolution": res_num,
                    "fanSub": fan_sub,
                    "isDub": is_dub
                })

            await page.close()

            semaphore = asyncio.Semaphore(3) 
            async def safe_extract(item):
                async with semaphore:
                    m3u8 = await self._extract_m3u8(item['embed'])
                    
                    clean_anime_title = anime_title.replace(" ", "_").replace("-", "") 
                    clean_anime_title = re.sub(r'_+', '_', clean_anime_title)
                    clean_fansub = item["fanSub"].replace(' ', '')

                    mp4_file_string = f"AnimePahe_{clean_anime_title}_-_{episode_num}_{item['resolution']}p_{clean_fansub}.mp4"
                    mp4_url = self._convert_m3u8_to_mp4(m3u8, mp4_file_string)
                    
                    return {
                        "url": m3u8,
                        "isM3U8": True,
                        "embed": item["embed"],
                        "resolution": item["resolution"],
                        "isDub": item["isDub"],
                        "fanSub": item["fanSub"],
                        "download": mp4_url
                    }

            sources = await asyncio.gather(*[safe_extract(item) for item in res_data])
            
            return {
                "ids": global_ids,
                "session": episode_session_id,
                "provider": "kwik",
                "episode": episode_num,
                "anime_title": anime_title,
                "sources": sources
            }
            
        except Exception as e:
            if not page.is_closed(): await page.close()
            return {"error": str(e)}

pahe = AnimePahe()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await pahe.start()
    yield
    await pahe.stop()

app = FastAPI(title="AnimePahe Extreme Universal Extractor API", lifespan=lifespan)

@app.get("/")
async def root(): return {"message": "AnimePahe Active. Engine operating cleanly via Main IO Pool!"}

@app.get("/search")
async def api_search(q: str): return await pahe.search(q)

@app.get("/latest")
async def api_latest(p: int = 1): return await pahe.get_latest_episodes(p)

@app.get("/info/{anime_session}")
async def api_info(anime_session: str, p: int = 1): return await pahe.get_anime_info(anime_session, p)

@app.get("/episodes/{anime_id}")
async def api_episodes(anime_id: str, p: int = 1): return await pahe.get_episodes(anime_id, p)

@app.get("/resolve/{anime_session}/{episode_session}")
async def api_resolve(anime_session: str, episode_session: str): return await pahe.get_links(anime_session, episode_session)

if __name__ == "__main__":
    import uvicorn
    # Dynamic port binding for Cloud Platforms (Render, Railway, Heroku, etc.)
    port = int(os.environ.get("PORT", 8000))
    script_name = os.path.splitext(os.path.basename(__file__))[0]
    uvicorn.run(f"{script_name}:app", host="0.0.0.0", port=port, workers=1)

import json
import asyncio
import re
import os
from typing import Optional, List
from contextlib import asynccontextmanager
from fastapi import FastAPI
from playwright.async_api import async_playwright, BrowserContext, Request

# --- CONFIG ---
BASE_URL = "https://animepahe.si"
IS_HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

class AnimePahe:
    def __init__(self):
        self.playwright = None
        self.context: Optional[BrowserContext] = None
        self.ad_domains = ["doubleclick.net", "adservice.google", "popads.net", "propellerads", "exoclick", "bebi.com"]

    async def start(self):
        self.playwright = await async_playwright().start()
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir="./browser_data",
            headless=IS_HEADLESS,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        await self.context.route("**/*", self._intercept_assets)

    async def _intercept_assets(self, route):
        url = route.request.url.lower()
        if any(ad in url for ad in self.ad_domains) or url.endswith((".png", ".jpg", ".css", ".woff")):
            await route.abort()
        else:
            await route.continue_()

    async def stop(self):
        if self.context: await self.context.close()
        if self.playwright: await self.playwright.stop()

    # --- SHARED HELPERS ---
    async def _fetch_json(self, url: str):
        page = await self.context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded")
            return json.loads(await page.evaluate("document.body.innerText"))
        except: return None
        finally: await page.close()

    def _generate_mp4(self, m3u8_url: Optional[str], anime_id: str, res: str) -> Optional[str]:
        if not m3u8_url: return None
        # Your working string replacement logic
        match = re.search(r'(https?://[^.]+)[^/]*/stream/(.*?)/[^/]+\.m3u8', m3u8_url)
        if match:
            return f"{match.group(1)}.kwik.cx/mp4/{match.group(2)}?file=AnimePahe_{anime_id}_{res}p.mp4"
        return None

    # --- ENDPOINTS ---
    async def search(self, q: str):
        data = await self._fetch_json(f"{BASE_URL}/api?m=search&q={q}")
        return data.get("data", []) if data else []

    async def get_latest(self, p: int = 1):
        return await self._fetch_json(f"{BASE_URL}/api?m=airing&page={p}")

    async def get_episodes(self, anime_id: str, p: int = 1):
        return await self._fetch_json(f"{BASE_URL}/api?m=release&id={anime_id}&sort=episode_desc&page={p}")

    async def get_info(self, session: str):
        page = await self.context.new_page()
        try:
            await page.goto(f"{BASE_URL}/anime/{session}", wait_until="domcontentloaded")
            content = await page.content()
            # Scrape basic metadata
            title = (re.search(r'<h1><span>(.*?)</span>', content) or re.search(r'<title>(.*?)</title>', content)).group(1)
            studio = (re.search(r'<strong>Studio:</strong>\s*(.*?)<', content) or [0, "Unknown"])[1]
            return {"title": title.strip(), "studio": studio.strip(), "session": session}
        finally: await page.close()

    # --- THE FIXED RESOLVER ---
    async def resolve(self, anime_session: str, episode_session: str):
        play_url = f"{BASE_URL}/play/{anime_session}/{episode_session}"
        page = await self.context.new_page()
        
        try:
            await page.goto(play_url, wait_until="domcontentloaded")
            await page.wait_for_selector("#resolutionMenu button", timeout=5000)
            
            buttons = await page.locator("#resolutionMenu button").all()
            res_data = []
            for btn in buttons:
                text = (await btn.inner_text()).strip()
                res_data.append({
                    "embed": await btn.get_attribute("data-src"),
                    "res": (re.search(r'(\d+)', text) or ["720"])[0],
                    "fanSub": text.split("·")[0].strip() if "·" in text else "Unknown"
                })
            await page.close()

            # Parallel resolution using the "Request Capture" method
            async def get_single_mp4(item):
                p = await self.context.new_page()
                m3u8 = None
                def log_req(req):
                    nonlocal m3u8
                    if ".m3u8" in req.url: m3u8 = req.url
                p.on("request", log_req)
                try:
                    await p.set_extra_http_headers({"Referer": BASE_URL})
                    await p.goto(item['embed'], wait_until="domcontentloaded")
                    # Force the player to trigger the m3u8 request
                    for _ in range(5):
                        if m3u8: break
                        await p.evaluate("document.querySelectorAll('button, video').forEach(el => el.click())")
                        await asyncio.sleep(0.5)
                    
                    item["url"] = m3u8
                    item["download"] = self._generate_mp4(m3u8, anime_session, item['res'])
                    return item
                finally: await p.close()

            sources = await asyncio.gather(*[get_single_mp4(i) for i in res_data])
            return {"anime": anime_session, "sources": sources}
        except Exception as e:
            return {"error": str(e)}

# --- FASTAPI SETUP ---
pahe = AnimePahe()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await pahe.start()
    yield
    await pahe.stop()

app = FastAPI(lifespan=lifespan)

@app.get("/search")
async def api_search(q: str): return await pahe.search(q)

@app.get("/latest")
async def api_latest(p: int = 1): return await pahe.get_latest(p)

@app.get("/info/{session}")
async def api_info(session: str): return await pahe.get_info(session)

@app.get("/episodes/{session}")
async def api_episodes(session: str, p: int = 1): return await pahe.get_episodes(session, p)

@app.get("/resolve/{anime}/{episode}")
async def api_resolve(anime: str, episode: str): return await pahe.resolve(anime, episode)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

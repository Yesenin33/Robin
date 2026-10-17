"""Regenerates the whole icon set from the one mark (public/mark.svg geometry). Run: python3 scripts/make_icons.py
Needs playwright (+ chromium) and Pillow. Outputs land in public/."""
import asyncio
import io
import pathlib

from PIL import Image
from playwright.async_api import async_playwright

PUB = pathlib.Path(__file__).resolve().parent.parent / "public"
INK, PAPER, LIME = "#110E08", "#F7F5EF", "#CCFF00"
GLYPH = (f'<path d="M22 10.5A8.5 8.5 0 1 0 22 21.5" fill="none" stroke="{LIME}" stroke-width="3" stroke-linecap="round"/>'
         f'<path d="M14 19l4-4 2 2 5-5.5" fill="none" stroke="{PAPER}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>')


def svg(size, rounded=True, glyph_scale=1.0):
    bg = f'<rect width="32" height="32" rx="{9 if rounded else 0}" fill="{INK}"/>'
    g = f'<g transform="translate(16 16) scale({glyph_scale}) translate(-16 -16)">{GLYPH}</g>'
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 32 32">{bg}{g}</svg>'


OG = f"""<html><body style="margin:0;width:1200px;height:630px;background:{PAPER};font-family:'Albert Sans',Inter,'Helvetica Neue',Arial,sans-serif;display:flex;align-items:center;position:relative;overflow:hidden">
<div style="position:absolute;left:0;right:0;top:-260px;height:560px;background:radial-gradient(ellipse 60% 50% at 50% 50%,rgba(204,255,0,.35),rgba(204,255,0,0))"></div>
<div style="padding:0 96px;display:flex;flex-direction:column;gap:34px;position:relative">
  <div style="display:flex;align-items:center;gap:20px">{svg(76)}<div style="font-family:Newsreader,Georgia,'DejaVu Serif',serif;font-size:52px;letter-spacing:-.02em;color:{INK}">Chainfolio</div></div>
  <div style="font-family:Newsreader,Georgia,'DejaVu Serif',serif;font-size:76px;line-height:1.04;letter-spacing:-.03em;color:{INK};max-width:960px">Your crypto portfolio on Robinhood Chain — <i style="background:linear-gradient(transparent 62%,{LIME} 62%);padding:0 .06em">simple and safe.</i></div>
  <div style="font-size:30px;color:#4A463D;font-weight:500">See what you own. Know what's risky before you buy.</div>
</div></body></html>"""


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome", args=["--no-sandbox"])
        pg = await b.new_page()

        async def png(markup, w, h, name):
            await pg.set_viewport_size({"width": w, "height": h})
            await pg.set_content(f"<body style='margin:0;background:transparent'>{markup}</body>")
            data = await pg.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": w, "height": h})
            (PUB / name).write_bytes(data)
            return data

        await png(svg(180, rounded=False), 180, 180, "apple-touch-icon.png")  # iOS rounds it itself
        await png(svg(192), 192, 192, "icon-192.png")
        await png(svg(512), 512, 512, "icon-512.png")
        await png(svg(512, rounded=False, glyph_scale=0.62), 512, 512, "icon-maskable-512.png")  # glyph inside the 80 % safe circle
        small = {s: Image.open(io.BytesIO(await png(svg(s), s, s, f"_fav{s}.png"))) for s in (16, 32, 48)}
        small[48].save(PUB / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)], append_images=[small[16], small[32]])
        for s_ in (16, 32, 48):
            (PUB / f"_fav{s_}.png").unlink()  # intermediates for the .ico only
        await pg.set_viewport_size({"width": 1200, "height": 630})
        await pg.set_content(OG)
        await pg.screenshot(path=str(PUB / "og.png"))
        await b.close()


asyncio.run(main())

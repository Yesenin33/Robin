"""Regenerates the whole icon set from the one mark (public/mark.svg geometry). Run: python3 scripts/make_icons.py
Needs playwright (+ chromium) and Pillow. Outputs land in public/."""
import asyncio
import io
import pathlib

from PIL import Image
from playwright.async_api import async_playwright

PUB = pathlib.Path(__file__).resolve().parent.parent / "public"
INK, GREEN = "#04210A", "#00C805"
GLYPH = f'<path d="M330.6 167.1A116 116 0 1 0 330.6 344.9" fill="none" stroke="{INK}" stroke-width="58" stroke-linecap="round"/><circle cx="356" cy="256" r="31" fill="{INK}"/>'


def svg(size, rounded=True, glyph_scale=1.0):
    bg = f'<rect width="512" height="512" rx="{120 if rounded else 0}" fill="{GREEN}"/>'
    g = f'<g transform="translate(256 256) scale({glyph_scale}) translate(-256 -256)">{GLYPH}</g>'
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 512 512">{bg}{g}</svg>'


OG = f"""<html><body style="margin:0;width:1200px;height:630px;background:#fff;font-family:Inter,'Helvetica Neue',Arial,sans-serif;display:flex;align-items:center;position:relative;overflow:hidden">
<div style="position:absolute;right:-180px;top:-220px;width:760px;height:760px;border-radius:50%;background:radial-gradient(circle,rgba(0,200,5,.22),rgba(0,200,5,0) 65%)"></div>
<div style="padding:0 96px;display:flex;flex-direction:column;gap:30px;position:relative">
  <div style="display:flex;align-items:center;gap:26px">{svg(112)}<div style="font-size:64px;font-weight:800;letter-spacing:-.025em;color:#0B0F10">Chainfolio</div></div>
  <div style="font-size:60px;line-height:1.08;font-weight:800;letter-spacing:-.03em;color:#0B0F10;max-width:880px">Your crypto on Robinhood Chain — <span style="color:#067A0A">simple and safe</span></div>
  <div style="font-size:30px;color:#4B565E;font-weight:500">See what you own. Know what's risky before you buy.</div>
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
        await png(svg(512, rounded=False, glyph_scale=0.66), 512, 512, "icon-maskable-512.png")  # glyph inside the 80 % safe circle
        small = {s: Image.open(io.BytesIO(await png(svg(s), s, s, f"_fav{s}.png"))) for s in (16, 32, 48)}
        small[48].save(PUB / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)], append_images=[small[16], small[32]])
        for s_ in (16, 32, 48):
            (PUB / f"_fav{s_}.png").unlink()  # intermediates for the .ico only
        await pg.set_viewport_size({"width": 1200, "height": 630})
        await pg.set_content(OG)
        await pg.screenshot(path=str(PUB / "og.png"))
        await b.close()


asyncio.run(main())

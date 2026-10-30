"""Offline packaged-app diagnostic: Paperport-1.1.2.exe --self-test."""
import base64
import tempfile
from pathlib import Path
from queue import Queue

import pymupdf
from playwright.sync_api import sync_playwright

from app import App, BrowserWorker


def run():
    window = App()
    window.withdraw()
    window.update_idletasks()
    window.native_icons.refresh()
    assert window.native_icons.dpi and len(window.native_icons.handles) == 2
    api = window.native_icons.user32
    hwnd = api.GetAncestor(window.winfo_id(), 2)
    for kind, handle in enumerate(window.native_icons.handles):
        actual = api.SendMessageW(hwnd, 0x7F, kind, 0)
        print(f'Icon {kind}: loaded={handle:#x}, attached={actual:#x}', flush=True)
        assert (actual & 0xFFFFFFFF) == (handle & 0xFFFFFFFF)
    assert window.brand_icon.cget('size') == (40, 40)
    print(f'Native icon DPI: {window.native_icons.dpi}; icon sizes: {window.native_icons.sizes}', flush=True)
    assert not hasattr(window, 'scan'), 'Automatic scrolling must not require a UI option.'
    window.after(100, window.close)
    window.mainloop()
    with tempfile.TemporaryDirectory() as temporary, sync_playwright() as playwright:
        browser = None
        for channel in ('msedge', 'chrome'):
            try:
                browser = playwright.chromium.launch(channel=channel, headless=True)
                break
            except Exception:
                continue
        if browser is None:
            raise RuntimeError('Install Edge or Chrome before running the diagnostic.')
        try:
            context = browser.new_context()
            images = []
            for number, color in ((1, 255), (2, 0)):
                pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 300), False)
                pix.clear_with(color)
                images.append((number, base64.b64encode(pix.tobytes('png')).decode()))
            html = '<html><head><meta charset="utf-8"></head><body>'
            for number, raw in images:
                label = 'صفحة ١ من ٢' if number == 1 else 'Page 2 of 2'
                html += f'<div><h2>{label}</h2><img id="page{number}"></div>'
                html += f'<script>document.getElementById("page{number}").src=URL.createObjectURL(new Blob([Uint8Array.from(atob("{raw}"), c=>c.charCodeAt(0))],{{type:"image/png"}}));</script>'
            html += '<script>for(let n=1;n<=2;n++){const i=document.getElementById("page"+n).cloneNode();i.id="noise"+n;document.body.appendChild(i);}document.body.appendChild(document.getElementById("page1").parentElement.cloneNode(true));</script>'
            html += '</body></html>'
            context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', headers={'Content-Security-Policy': "connect-src 'none'"}, body=html))
            page = context.new_page()
            page.goto('https://drive.google.com/file/d/paperport-self-test/view')
            page.wait_for_function('Array.from(document.images).length === 5 && Array.from(document.images).every(i=>i.complete && i.naturalWidth>0)')
            worker = BrowserWorker(Queue())
            worker.context = context
            output = Path(temporary) / 'test.pdf'
            worker.export({'path': str(output), 'page_timeout': 2})
            with pymupdf.open(output) as pdf:
                assert len(pdf) == 2
                assert pdf[0].get_pixmap().pixel(10, 10) == (255, 255, 255)
                assert pdf[1].get_pixmap().pixel(10, 10) == (0, 0, 0)
            print('Hidden UI, bundled browser driver, automatic capture, and PDF rendering passed.', flush=True)
        finally:
            browser.close()

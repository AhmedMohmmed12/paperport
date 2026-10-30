import base64
import os
import queue
import sys
import threading
import time
from pathlib import Path
from tkinter import filedialog, messagebox

# Some Windows Python distributions do not discover their bundled Tcl scripts.
_tcl_root = Path(sys.base_prefix) / 'tcl'
if (_tcl_root / 'tcl8.6' / 'init.tcl').is_file():
    os.environ.setdefault('TCL_LIBRARY', str(_tcl_root / 'tcl8.6'))
    os.environ.setdefault('TK_LIBRARY', str(_tcl_root / 'tk8.6'))

import customtkinter as ctk
from PIL import Image
from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout

from core import (PdfBuilder, CapturedPages, drive_url, LIST_IMAGES, READ_IMAGES,
                  SCROLL, PAGE_CONTROL, READY_PAGE)
from paths import data_dir, asset_path
from window_icons import WindowIcons


class BrowserWorker(threading.Thread):
    def __init__(self, events):
        super().__init__(daemon=True)
        self.events = events
        self.commands = queue.Queue()
        self.cancel = threading.Event()
        self.context = None
        self.visible = False

    def emit(self, kind, data):
        self.events.put((kind, data))

    def run(self):
        with sync_playwright() as playwright:
            while True:
                command, data = self.commands.get()
                try:
                    if command == 'quit':
                        break
                    if command == 'open':
                        self.open_document(playwright, data['url'], data.get('visible', False))
                    elif command == 'export':
                        self.export(data)
                except Exception as exc:
                    self.emit('error', str(exc))
            if self.context:
                self.context.close()

    def open_document(self, playwright, url, visible=False):
        if self.context is not None and self.visible != visible:
            self.context.close()
            self.context = None
        if self.context is None:
            for channel in ('msedge', 'chrome'):
                try:
                    self.context = playwright.chromium.launch_persistent_context(
                        str(data_dir() / 'browser-profile'), channel=channel,
                        headless=not visible,
                        viewport={'width': 1440, 'height': 1100},
                    )
                    self.context.on('close', lambda: setattr(self, 'context', None))
                    self.visible = visible
                    break
                except Exception:
                    continue
            if self.context is None:
                raise RuntimeError('Could not start the browser. Close other Paperport windows and retry. Install Edge or Chrome if neither is available.')
        page = self.context.pages[0] if self.context.pages else self.context.new_page()
        page.goto(url, wait_until='domcontentloaded', timeout=60000)
        if visible:
            page.bring_to_front()
            self.emit('ready', 'Browser open for sign-in. Once the PDF appears, turn off Show browser and load it again to export in the background.')
        else:
            try:
                page.locator('img[src^="blob:https://drive.google.com/"]').first.wait_for(state='attached', timeout=45000)
            except BrowserTimeout as exc:
                raise ValueError('The PDF did not load in the background. If it needs sign-in, enable Show browser, load the link and sign in. Then turn it off and load again.') from exc
            self.emit('ready', 'Document loaded in the background. Choose Export PDF to save it.')

    def export(self, options):
        if not self.context:
            raise ValueError('Open a Drive file first.')
        pages = [p for p in self.context.pages if not p.is_closed() and p.url.startswith('https://drive.google.com/')]
        if not pages:
            raise ValueError('The Drive browser tab is closed. Open the file again.')
        page = pages[-1]
        builder = PdfBuilder()
        captured = CapturedPages()
        started = time.monotonic()
        timeout = options.get('page_timeout', 8)

        def cancelled():
            if self.cancel.is_set():
                self.emit('cancelled', 'Export cancelled. No output file was written.')
                return True
            return False

        try:
            frames = [f for f in page.frames if f.url.startswith('https://drive.google.com/')]
            navigator, navigation_frame = None, None
            for frame in frames:
                control = frame.evaluate(PAGE_CONTROL)
                if control and type(control.get('index')) is int:
                    navigator = frame.locator('input[inputmode="numeric"]').nth(control['index'])
                    navigation_frame = frame
                    captured.expected = control['total']
                    break
            if navigator is None:
                for frame in frames:
                    frame.evaluate(SCROLL, True)
            bottom_since = None
            attempts = {}
            for _ in range(1500):
                if cancelled():
                    return
                snapshots = [(frame, frame.evaluate(LIST_IMAGES)) for frame in frames]
                captured.observe([i for _, infos in snapshots for i in infos])
                before = len(captured)
                for frame, infos in snapshots:
                    wanted = [info for info in infos if captured.wanted(info, (frame, info['src']))]
                    for offset in range(0, len(wanted), 4):
                        if cancelled():
                            return
                        batch = wanted[offset:offset+4]
                        results = frame.evaluate(READ_IMAGES, [info['src'] for info in batch])
                        by_src = {result['src']: result for result in results}
                        for info in batch:
                            result = by_src.get(info['src'], {})
                            if result.get('data'):
                                captured.add(info, (frame, info['src']), base64.b64decode(result['data']))
                        self.emit('progress', (len(captured), time.monotonic() - started, captured.expected))
                if captured.complete():
                    break
                if navigator is not None:
                    target = captured.missing()[0]
                    attempts[target] = attempts.get(target, 0) + 1
                    if attempts[target] > 2:
                        captured.ordered()  # Raise an error identifying the missing page numbers.
                    navigator.fill(str(target), timeout=5000)
                    navigator.press('Enter', timeout=5000)
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        if cancelled():
                            return
                        try:
                            navigation_frame.wait_for_function(READY_PAGE, arg=target, timeout=250, polling=40)
                            break
                        except BrowserTimeout:
                            continue
                    continue
                positions = [f.evaluate(SCROLL, False) for f in frames]
                at_bottom = all(p['bottom'] for p in positions)
                if at_bottom and len(captured) == before:
                    bottom_since = bottom_since or time.monotonic()
                    if time.monotonic() - bottom_since >= min(timeout, 2):
                        break
                else:
                    bottom_since = None
                self.cancel.wait(.04)
            else:
                raise ValueError('Scan reached its safety limit. Try a smaller document.')
            if cancelled():
                return
            self.emit('status', 'All pages captured. Compressing and saving the PDF…')
            for raw in captured.ordered():
                if cancelled():
                    return
                builder.add(raw)
            builder.save(options['path'])
            self.emit('done', (options['path'], len(captured), time.monotonic() - started))
        finally:
            builder.close()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.iconbitmap(default=str(asset_path('paperport.ico')))
        self.title('Paperport 1.1.2 | Drive to PDF')
        self.native_icons = WindowIcons(self, asset_path('paperport.ico'))
        self.geometry('960x760')
        self.minsize(780, 700)
        self.configure(fg_color='#101318')
        self.events = queue.Queue()
        self.worker = BrowserWorker(self.events)
        self.worker.start()
        self.output = None
        self.ready = False
        self.busy = False
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.grid_columnconfigure(0, weight=1)
        main = ctk.CTkFrame(self, fg_color='transparent')
        main.grid(row=0, column=0, sticky='nsew', padx=42, pady=30)
        main.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(main, fg_color='transparent')
        top.grid(row=0, column=0, sticky='ew')
        with Image.open(asset_path('paperport.png')) as image:
            self.brand_icon = ctk.CTkImage(light_image=image.copy(), dark_image=image.copy(), size=(40, 40))
        ctk.CTkLabel(top, image=self.brand_icon, text='', width=40).pack(side='left', padx=(0, 10))
        ctk.CTkLabel(top, text='PAPERPORT 1.1.2', font=('Segoe UI', 18, 'bold'), text_color='#b7f7ca').pack(side='left')
        ctk.CTkLabel(top, text='DRIVE → PDF', text_color='#858d9b', font=('Segoe UI', 12)).pack(side='right')
        ctk.CTkLabel(main, text='Your pages. One PDF.', font=('Segoe UI', 37, 'bold'), anchor='w').grid(row=1, column=0, sticky='ew', pady=(35, 5))
        ctk.CTkLabel(main, text='Save the page images in your Drive viewer as a clean, local PDF.', text_color='#a5adba', font=('Segoe UI', 15), anchor='w').grid(row=2, column=0, sticky='ew', pady=(0, 25))
        card = ctk.CTkFrame(main, fg_color='#1a1f28', corner_radius=18, border_width=1, border_color='#2b3340')
        card.grid(row=3, column=0, sticky='ew')
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text='01   OPEN YOUR DOCUMENT', font=('Segoe UI', 12, 'bold'), text_color='#b7f7ca', anchor='w').grid(row=0, column=0, sticky='ew', padx=24, pady=(22, 12))
        self.link = ctk.CTkEntry(card, height=48, placeholder_text='https://drive.google.com/file/d/…/view', font=('Segoe UI', 14), fg_color='#101318', border_color='#394252')
        self.link.grid(row=1, column=0, sticky='ew', padx=24)
        load_actions = ctk.CTkFrame(card, fg_color='transparent')
        load_actions.grid(row=2, column=0, sticky='ew', padx=24, pady=(14, 20))
        self.open_button = ctk.CTkButton(load_actions, text='Load document', height=42, fg_color='#b7f7ca', hover_color='#9ce3b2', text_color='#10241a', font=('Segoe UI', 14, 'bold'), command=self.open_drive)
        self.open_button.pack(side='left')
        self.show_browser = ctk.CTkSwitch(load_actions, text='Show browser for sign-in', progress_color='#4b9670', font=('Segoe UI', 13))
        self.show_browser.pack(side='left', padx=(20, 0))
        ctk.CTkLabel(card, text='02   CAPTURE & SAVE', font=('Segoe UI', 12, 'bold'), text_color='#b7f7ca', anchor='w').grid(row=3, column=0, sticky='ew', padx=24, pady=(0, 10))
        self.speed = ctk.CTkOptionMenu(card, values=['Automatic · adaptive loading', 'Patient · slow connections'], fg_color='#303a49', button_color='#465366', width=255)
        self.speed.grid(row=4, column=0, sticky='w', padx=24, pady=(0, 15))
        actions = ctk.CTkFrame(card, fg_color='transparent')
        actions.grid(row=5, column=0, sticky='ew', padx=24, pady=(0, 22))
        self.save_button = ctk.CTkButton(actions, text='Export PDF  ↓', height=46, width=200, state='disabled', fg_color='#b7f7ca', hover_color='#9ce3b2', text_color='#10241a', font=('Segoe UI', 15, 'bold'), command=self.export)
        self.save_button.pack(side='left')
        self.cancel_button = ctk.CTkButton(actions, text='Cancel', height=46, width=90, state='disabled', fg_color='#303a49', command=self.worker.cancel.set)
        self.cancel_button.pack(side='left', padx=12)
        self.folder_button = ctk.CTkButton(actions, text='Show file', height=46, width=100, state='disabled', fg_color='#303a49', command=self.show_file)
        self.folder_button.pack(side='right')
        self.progress = ctk.CTkProgressBar(main, height=5, progress_color='#b7f7ca', fg_color='#2b3340')
        self.progress.grid(row=4, column=0, sticky='ew', pady=(24, 14))
        self.progress.set(0)
        self.status = ctk.CTkLabel(main, text='Ready when you are. Paste a Drive PDF link to begin.', anchor='w', justify='left', wraplength=800, text_color='#a5adba', font=('Segoe UI', 13))
        self.status.grid(row=5, column=0, sticky='ew')
        ctk.CTkLabel(main, text='LOCAL EXPORT  /  Native PDF assembly. Lossless page images.\nImage-based output: text, links, and annotations are not preserved. Review page count after export.', anchor='w', justify='left', text_color='#788393', font=('Segoe UI', 12)).grid(row=6, column=0, sticky='ew', pady=(22, 0))
        self.after(100, self.poll)

    def set_busy(self, busy):
        self.busy = busy
        self.open_button.configure(state='disabled' if busy else 'normal')
        self.show_browser.configure(state='disabled' if busy else 'normal')
        self.save_button.configure(state='normal' if self.ready and not busy else 'disabled')
        self.cancel_button.configure(state='normal' if busy else 'disabled')
        if busy:
            self.progress.configure(mode='indeterminate')
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.configure(mode='determinate')

    def open_drive(self):
        try:
            url = drive_url(self.link.get())
        except ValueError as exc:
            messagebox.showerror('Check your link', str(exc))
            return
        self.worker.cancel.clear()
        self.ready = False
        self.set_busy(True)
        visible = bool(self.show_browser.get())
        self.status.configure(text='Opening browser for sign-in…' if visible else 'Loading the document in the background…', text_color='#a5adba')
        self.worker.commands.put(('open', {'url': url, 'visible': visible}))

    def export(self):
        path = filedialog.asksaveasfilename(title='Save PDF', defaultextension='.pdf', initialfile='Drive document.pdf', filetypes=[('PDF document', '*.pdf')])
        if not path:
            return
        timeout = 20 if self.speed.get().startswith('Patient') else 8
        self.worker.cancel.clear()
        self.set_busy(True)
        self.status.configure(text='Scanning the viewer for loaded page images…')
        self.worker.commands.put(('export', {'path': path, 'page_timeout': timeout}))

    def poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == 'status':
                    self.status.configure(text=data, text_color='#a5adba')
                elif kind == 'progress':
                    total = f' / {data[2]}' if data[2] else ''
                    self.status.configure(text=f'{data[0]}{total} page images captured  ·  {data[1]:.1f}s elapsed', text_color='#a5adba')
                    if data[2]:
                        self.progress.stop()
                        self.progress.configure(mode='determinate')
                        self.progress.set(min(1, data[0] / data[2]))
                else:
                    self.set_busy(False)
                    if kind == 'ready':
                        self.ready = True
                        self.save_button.configure(state='normal')
                    if kind == 'done':
                        self.output, count, seconds = data
                        data = f'Saved {count} image pages in {seconds:.1f}s. Review the PDF to confirm every page is present.\n{self.output}'
                        self.progress.set(1)
                        self.folder_button.configure(state='normal')
                    else:
                        self.progress.set(0)
                    self.status.configure(text=data, text_color='#f2b7a6' if kind == 'error' else '#a5adba')
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def show_file(self):
        if self.output:
            import subprocess
            subprocess.Popen(['explorer.exe', '/select,', str(Path(self.output))])

    def close(self):
        self.worker.cancel.set()
        self.worker.commands.put(('quit', None))
        self.withdraw()
        self.after(100, self.wait_close)

    def wait_close(self):
        if self.worker.is_alive():
            self.after(100, self.wait_close)
        else:
            self.destroy()

    def destroy(self):
        if hasattr(self, 'native_icons'):
            self.native_icons.close()
        super().destroy()

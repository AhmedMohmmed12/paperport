import tempfile
import unittest
from pathlib import Path
from queue import Queue

import pymupdf
from core import PdfBuilder, CapturedPages, drive_url, LIST_IMAGES, READ_IMAGES, SCROLL
from app import BrowserWorker


def image(width, height, color):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.clear_with(color)
    return pix.tobytes('png')


class CoreTests(unittest.TestCase):
    def test_urls(self):
        for url in ('https://drive.google.com/file/d/abc-123/view?usp=sharing', 'https://drive.google.com/open?id=abc'):
            self.assertEqual(drive_url(url), url)
        for url in ('http://drive.google.com/file/d/a/view', 'https://drive.google.com.evil.test/file/d/a/view', 'https://drive.google.com/drive/folders/a', 'javascript:alert(1)'):
            with self.assertRaises(ValueError):
                drive_url(url)

    def test_pdf_pages_and_pixel_fidelity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'mixed.pdf'
            builder = PdfBuilder()
            try:
                builder.add(image(200, 300, 255))
                builder.add(image(400, 200, 0))
                builder.save(path)
            finally:
                builder.close()
            with pymupdf.open(path) as pdf:
                self.assertEqual(len(pdf), 2)
                self.assertEqual(tuple(pdf[0].rect), (0, 0, 150, 225))
                self.assertEqual(tuple(pdf[1].rect), (0, 0, 300, 150))
                self.assertEqual(pdf[0].get_pixmap().pixel(20, 20), (255, 255, 255))
                self.assertEqual(pdf[1].get_pixmap().pixel(20, 20), (0, 0, 0))
            self.assertFalse(list(Path(tmp).glob('*.partial')))

    def test_empty_export_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'existing.pdf'
            path.write_bytes(b'keep')
            builder = PdfBuilder()
            try:
                with self.assertRaises(ValueError):
                    builder.save(path)
            finally:
                builder.close()
            self.assertEqual(path.read_bytes(), b'keep')

    def test_export_deduplicates_same_blob_and_cancels(self):
        import base64
        raw = base64.b64encode(image(200, 300, 255)).decode()

        class Frame:
            url = 'https://drive.google.com/file/d/a/view'
            def evaluate(self, script, arg=None):
                if script == LIST_IMAGES:
                    return [{'src': 'blob:https://drive.google.com/page1'}] * 2
                if script == READ_IMAGES:
                    return [{'src': src, 'data': raw} for src in arg]
                return {'bottom': True, 'progress': 1}

        class Page:
            url = Frame.url
            frames = [Frame()]
            def is_closed(self):
                return False

        class Context:
            pages = [Page()]

        worker = BrowserWorker(Queue())
        worker.context = Context()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'out.pdf'
            worker.export({'path': str(path), 'page_timeout': .01})
            with pymupdf.open(path) as pdf:
                self.assertEqual(len(pdf), 1)
            worker.cancel.set()
            other = Path(tmp) / 'cancelled.pdf'
            worker.export({'path': str(other), 'page_timeout': .01})
            self.assertFalse(other.exists())

    def test_incomplete_numbered_scan_does_not_save(self):
        import base64
        raw = base64.b64encode(image(100, 150, 255)).decode()

        class Frame:
            url = 'https://drive.google.com/file/d/a/view'
            def evaluate(self, script, arg=None):
                if script == LIST_IMAGES:
                    return [{'src': 'blob:https://drive.google.com/one', 'page': 1, 'total': 2}]
                if script == READ_IMAGES:
                    return [{'src': src, 'data': raw} for src in arg]
                return {'bottom': True, 'progress': 1}

        class Page:
            url = Frame.url
            frames = [Frame()]
            def is_closed(self):
                return False

        class Context:
            pages = [Page()]

        worker = BrowserWorker(Queue())
        worker.context = Context()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'missing.pdf'
            with self.assertRaisesRegex(ValueError, '1 of 2'):
                worker.export({'path': str(path), 'page_timeout': .01})
            self.assertFalse(path.exists())

    def test_138_pages_plus_viewer_assets_and_duplicate_frame(self):
        pages = CapturedPages()
        infos = [{'src': f'page-{n}', 'page': n, 'total': 138} for n in range(138, 0, -1)]
        noise = [{'src': 'unlabelled-one'}, {'src': 'unlabelled-two'}]
        pages.observe(noise + infos)
        for frame in ('main', 'duplicate-viewer'):
            for info in noise + infos:
                pages.add(info, (frame, info['src']), str(info.get('page')).encode())
        self.assertEqual(len(pages), 138)
        self.assertTrue(pages.complete())
        self.assertEqual(pages.ordered(), [str(n).encode() for n in range(1, 139)])

    def test_late_numbering_discards_earlier_unlabelled_images(self):
        pages = CapturedPages()
        pages.add({'src': 'noise'}, 'noise', b'not-a-page')
        info = {'src': 'page', 'page': 1, 'total': 1}
        pages.observe([info])
        pages.add(info, 'page', b'page-one')
        self.assertEqual(pages.ordered(), [b'page-one'])

    def test_invalid_page_numbers_do_not_count_as_document_pages(self):
        pages = CapturedPages(2)
        for number in (None, 0, 3, -1):
            pages.add({'page': number, 'total': 2}, number, b'bad')
        self.assertEqual(len(pages), 0)


if __name__ == '__main__':
    unittest.main()

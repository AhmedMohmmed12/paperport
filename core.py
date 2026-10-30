"""Assemble viewer images with native PDF code and a lossless browser fallback."""
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pymupdf


def drive_url(value):
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme != 'https' or parts.hostname != 'drive.google.com' or parts.username or parts.password:
        raise ValueError('Paste an HTTPS file link from drive.google.com.')
    match = re.fullmatch(r'/file/d/([\w-]+)(?:/(?:view|preview|edit))?/?', parts.path)
    file_id = match.group(1) if match else (parse_qs(parts.query).get('id', [''])[0] if parts.path in ('/open', '/uc') else '')
    if not re.fullmatch(r'[\w-]+', file_id):
        raise ValueError('Use a Drive file link, not a folder link.')
    return value


class PdfBuilder:
    def __init__(self):
        self.doc = pymupdf.open()

    def add(self, data):
        # Embed image bytes using the original pixel dimensions.
        pix = pymupdf.Pixmap(data)
        width, height = pix.width * .75, pix.height * .75
        del pix
        page = self.doc.new_page(width=width, height=height)
        page.insert_image(page.rect, stream=data)

    def save(self, destination):
        if not len(self.doc):
            raise ValueError('No page images were found. Open the PDF and scroll until its pages load.')
        destination = Path(destination)
        fd, temporary = tempfile.mkstemp(suffix='.partial', dir=destination.parent)
        os.close(fd)
        try:
            self.doc.save(temporary, garbage=0, deflate=True, deflate_images=True)
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def close(self):
        self.doc.close()


class CapturedPages:
    """Numbered document pages take precedence over unnumbered viewer assets."""
    def __init__(self, expected=None):
        self.expected = expected
        self.numbered = {}
        self.unnumbered = {}

    def observe(self, infos):
        if self.expected is None:
            totals = Counter(i['total'] for i in infos if self.valid_number(i))
            if totals:
                self.expected = totals.most_common(1)[0][0]
                self.unnumbered.clear()

    @staticmethod
    def valid_number(info):
        number, total = info.get('page'), info.get('total')
        return type(number) is int and type(total) is int and 1 <= number <= total

    def wanted(self, info, key):
        if self.expected is not None:
            return self.valid_number(info) and info['total'] == self.expected and info['page'] not in self.numbered
        return key not in self.unnumbered

    def add(self, info, key, raw):
        if self.wanted(info, key):
            if self.expected is not None:
                self.numbered[info['page']] = raw
            else:
                self.unnumbered[key] = raw

    def __len__(self):
        return len(self.numbered) if self.expected is not None else len(self.unnumbered)

    def missing(self):
        return sorted(set(range(1, self.expected + 1)) - self.numbered.keys()) if self.expected else []

    def complete(self):
        return self.expected is not None and not self.missing()

    def ordered(self):
        missing = self.missing()
        if missing:
            preview = ', '.join(map(str, missing[:12])) + ('…' if len(missing) > 12 else '')
            raise ValueError(f'Captured {len(self)} of {self.expected} pages. Missing pages: {preview}. No file was written. Try Patient mode.')
        return [self.numbered[n] for n in sorted(self.numbered)] if self.expected else list(self.unnumbered.values())


LIST_IMAGES = """() => Array.from(document.images)
  .filter(i => i.src.startsWith('blob:https://drive.google.com/') && i.complete && i.naturalWidth > 0 && i.naturalHeight > 0)
  .map(i => {
    let heading = '';
    for(let p=i.parentElement, depth=0; p && depth<4; p=p.parentElement,depth++) {
      const hs=p.querySelectorAll('h2');
      if(hs.length===1) { heading=hs[0].textContent; break; }
      if(hs.length>1) break;
    }
    const normalized=heading.replace(/[٠-٩]/g,c=>String(c.charCodeAt(0)-1632)).replace(/[۰-۹]/g,c=>String(c.charCodeAt(0)-1776));
    const numbers = normalized.match(/\\d+/g) || [];
    return {src:i.src, width:i.naturalWidth, height:i.naturalHeight,
      page: numbers.length === 2 ? Number(numbers[0]) : null,
      total: numbers.length === 2 ? Number(numbers[1]) : null};
  })"""

READ_IMAGE = """async src => {
  const image = Array.from(document.images).find(i => i.src === src);
  if (!image) throw new Error('Page image unloaded. Try scanning again.');
  if(window.__paperportBlobFetchAllowed !== false) try {
    const response = await fetch(src);
    if (!response.ok) throw new Error('Could not read image bytes.');
    const blob = await response.blob();
    return await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error('Could not read image bytes.'));
      reader.onload = () => resolve(reader.result.split(',')[1]);
      reader.readAsDataURL(blob);
    });
  } catch (_) { window.__paperportBlobFetchAllowed = false; }
  {
    const canvas = document.createElement('canvas');
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    canvas.getContext('2d').drawImage(image, 0, 0);
    const blob=await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error('Image encoding failed.')),'image/png'));
    return await new Promise((resolve,reject)=>{
      const reader=new FileReader(); reader.onerror=()=>reject(new Error('Image read failed.'));
      reader.onload=()=>resolve(reader.result.split(',')[1]); reader.readAsDataURL(blob);
    });
  }
}"""

READ_IMAGES = "async srcs => Promise.all(srcs.map(async src => { try { return {src, data:await (" + READ_IMAGE + ")(src)}; } catch(e) { return {src,error:String(e)}; } }))"

PAGE_CONTROL = """() => {
  const inputs=Array.from(document.querySelectorAll('input[inputmode="numeric"]'));
  for(let index=0;index<inputs.length;index++) {
    const text=(inputs[index].getAttribute('aria-label')||'').replace(/[٠-٩]/g,c=>String(c.charCodeAt(0)-1632)).replace(/[۰-۹]/g,c=>String(c.charCodeAt(0)-1776));
    const nums=text.match(/\\d+/g)||[];
    if(nums.length===2 && Number(nums[0])>=1 && Number(nums[1])>=Number(nums[0])) return {index,total:Number(nums[1])};
  }
  return null;
}"""

READY_PAGE = "number => (" + LIST_IMAGES + ")().some(i=>i.page===number)"

SCROLL = """reset => {
  let el=window.__paperportScroller;
  if(!el || !el.isConnected) {
  let nodes = [document.scrollingElement, ...document.querySelectorAll('*')].filter(Boolean);
  el = nodes.filter(e => e.clientHeight > 150 && e.scrollHeight > e.clientHeight + 40
      && (e === document.scrollingElement || /auto|scroll/.test(getComputedStyle(e).overflowY)))
    .sort((a,b) => (b.scrollHeight-b.clientHeight)-(a.scrollHeight-a.clientHeight))[0];
  window.__paperportScroller=el;
  }
  if (!el) return {bottom:true, progress:1};
  if(reset) el.scrollTop = 0;
  else el.scrollTop = Math.min(el.scrollTop + el.clientHeight * .9, el.scrollHeight);
  return {bottom: el.scrollTop + el.clientHeight >= el.scrollHeight - 4,
    progress: (el.scrollTop + el.clientHeight) / el.scrollHeight};
}"""
